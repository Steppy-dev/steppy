"""Where a run came from: commit, branch, pull request, CI job, environment (spec I3).

A **leaf**: stdlib only, importing nothing from ``steppy``. ``steppy run`` resolves
a run's metadata before any test runs (it is on the eager CLI path), the bundle
commands resolve it again for a run that never recorded any, and ``models.py``
validates ``config.yaml``'s ``environment:`` key with :func:`clean_environment`.
One home, so the three cannot disagree.

**Resolution, field by field:** an explicit flag (``--branch``, ``--commit``,
``--pr``, ``--environment``), then the CI's variables, then the local repository,
then nothing. The environment has its own ladder: ``--environment``, then
``STEPPY_ENVIRONMENT``, then ``config.yaml``'s ``environment:``.

Two CIs are detected, and no other:

- **GitHub Actions.** On ``pull_request`` and ``pull_request_target`` the branch is
  ``GITHUB_HEAD_REF`` and the commit is the pull request's head, read from the
  event file (``GITHUB_EVENT_PATH``) — ``GITHUB_SHA`` there is the merge commit
  GitHub builds to test the request (the base branch's tip on
  ``pull_request_target``), which goes in ``tested_sha``. A push keeps
  ``GITHUB_REF_NAME`` and ``GITHUB_SHA``; a tag has no branch. The link is the
  workflow run's attempt: GitHub gives no job URL without its API.
- **GitLab CI.** The merge request's source branch, else ``CI_COMMIT_BRANCH``
  (unset on a tag; ``CI_COMMIT_REF_NAME`` is never read, because on a tag it is the
  tag). The commit is ``CI_MERGE_REQUEST_SOURCE_BRANCH_SHA`` when set — merged-results
  pipelines, where ``CI_COMMIT_SHA`` is the merge — else ``CI_COMMIT_SHA``.

An empty variable is an unset one: a CI often defines a variable as empty rather
than leaving it out. A CI file that cannot be read degrades to what the
environment says, with a warning, and never fails the run.

**``tested_sha`` and ``dirty`` describe the checkout against the commit that was
detected**, so a ``--commit`` naming another one drops both: nothing here can vouch
for what was checked out relative to a commit the user named.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

#: ``runs.environment``'s length in a control plane, and the cap here, so a
#: value is refused before the tests rather than cut by a server after them.
ENVIRONMENT_MAX = 64
#: Longer than any branch a forge accepts; a control plane refuses longer.
BRANCH_MAX = 255

ENVIRONMENT_VAR = "STEPPY_ENVIRONMENT"

EXECUTOR_CI = "ci"
EXECUTOR_LOCAL = "local"

PROVIDER_GITHUB = "github-actions"
PROVIDER_GITLAB = "gitlab-ci"

#: Hex, abbreviated (git accepts four characters) up to a SHA-256 object name.
_COMMIT = re.compile(r"^[0-9a-fA-F]{4,64}$")
#: An unresolved ``${VAR}`` left in a config value by ``config._interpolate_env``.
_UNRESOLVED_REF = re.compile(r"\$\{[A-Za-z_]\w*(?::-[^}]*)?\}")

_FALSY = frozenset({"", "0", "false", "no", "off"})


class MetadataError(ValueError):
    """A run metadata value that cannot be used. Fatal before any test runs."""


@dataclass(frozen=True)
class Git:
    """``run.git`` of the bundle contract."""

    sha: Optional[str] = None
    branch: Optional[str] = None
    pr: Optional[int] = None
    base_branch: Optional[str] = None
    tested_sha: Optional[str] = None
    dirty: Optional[bool] = None


@dataclass(frozen=True)
class Ci:
    """``run.ci`` of the bundle contract."""

    provider: str
    url: Optional[str] = None
    repo: Optional[str] = None


@dataclass(frozen=True)
class RunMetadata:
    """Everything a run says about where it came from."""

    executor: str = EXECUTOR_LOCAL
    environment: Optional[str] = None
    git: Git = field(default_factory=Git)
    ci: Optional[Ci] = None
    #: Degraded sources, for the manifest's ``warnings`` (an unreadable event file).
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The contract's shape: ``executor``, ``environment``, ``git``, ``ci``."""
        return {
            "executor": self.executor,
            "environment": self.environment,
            "git": asdict(self.git),
            "ci": asdict(self.ci) if self.ci is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunMetadata":
        """Read :meth:`as_dict`'s shape back, tolerant of anything missing or odd.

        What it reads was written by a process that may be older or newer (a
        run sidecar, an archive's manifest), so a field of the wrong type is
        treated as absent rather than as an error.
        """
        raw_git = data.get("git") if isinstance(data.get("git"), Mapping) else {}
        raw_ci = data.get("ci") if isinstance(data.get("ci"), Mapping) else None
        pr = raw_git.get("pr")
        dirty = raw_git.get("dirty")
        git = Git(
            sha=_text(raw_git.get("sha")),
            branch=_text(raw_git.get("branch")),
            pr=pr if isinstance(pr, int) and not isinstance(pr, bool) else None,
            base_branch=_text(raw_git.get("base_branch")),
            tested_sha=_text(raw_git.get("tested_sha")),
            dirty=dirty if isinstance(dirty, bool) else None,
        )
        ci = None
        if raw_ci is not None and _text(raw_ci.get("provider")):
            ci = Ci(provider=raw_ci["provider"], url=_text(raw_ci.get("url")), repo=_text(raw_ci.get("repo")))
        warnings = data.get("metadata_warnings")
        return cls(
            executor=_text(data.get("executor")) or EXECUTOR_LOCAL,
            environment=_text(data.get("environment")),
            git=git,
            ci=ci,
            warnings=tuple(w for w in warnings if isinstance(w, str)) if isinstance(warnings, list) else (),
        )

    def sidecar_fields(self) -> dict[str, Any]:
        """What ``output/.steppy-run.json`` records beside ``executor``.

        Always carries ``git``, even all-null: its presence is how a reader tells
        a run that recorded its metadata (and found none) from a run made before
        it did, which must be detected at bundle time instead.
        """
        payload = {k: v for k, v in self.as_dict().items() if k != "executor"}
        if self.warnings:
            payload["metadata_warnings"] = list(self.warnings)
        return payload

    def describe(self) -> str:
        """One line for the console: ``git 1a2b3c4d5e6f on main (PR #12) · ci github-actions · environment staging``."""
        parts = []
        if self.git.sha or self.git.branch:
            text = f"git {self.git.sha[:12]}" if self.git.sha else "git"
            if self.git.branch:
                text += f" on {self.git.branch}"
            if self.git.pr is not None:
                text += f" (PR #{self.git.pr}" + (f" into {self.git.base_branch})" if self.git.base_branch else ")")
            if self.git.dirty:
                text += ", uncommitted changes"
            parts.append(text)
        parts.append(f"{self.executor}" + (f" {self.ci.provider}" if self.ci else ""))
        if self.environment:
            parts.append(f"environment {self.environment}")
        return " · ".join(parts)


def from_sidecar(sidecar: Optional[Mapping[str, Any]]) -> Optional[RunMetadata]:
    """The metadata a run recorded in ``output/.steppy-run.json``, or None.

    None when there is no sidecar, or when it was written by a Steppy that did not
    record metadata (before 0.10): its ``executor`` was always ``local`` then, so
    it is not evidence of anything.
    """
    if not sidecar or "git" not in sidecar:
        return None
    return RunMetadata.from_dict(sidecar)


# ── Values ──────────────────────────────────────────────────────────────────


def _text(value: Any) -> Optional[str]:
    """A non-empty, stripped string, else None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _env(env: Mapping[str, str], name: str) -> Optional[str]:
    return _text(env.get(name))


def _control_character(value: str) -> Optional[str]:
    for char in value:
        if unicodedata.category(char).startswith("C"):
            return f"U+{ord(char):04X}"
    return None


def clean_environment(value: Any, *, source: str = "--environment") -> Optional[str]:
    """*value* as a run's environment: stripped, at most 64 characters, printable.

    None when empty, or when it is a ``${VAR}`` reference ``config.yaml`` could not
    resolve (``environment: ${DEPLOY_ENV}`` with ``DEPLOY_ENV`` unset is no
    environment, not one named ``${DEPLOY_ENV}``). Raises :class:`MetadataError`
    naming *source* otherwise.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise MetadataError(f"{source} must be text, not {type(value).__name__} ({value!r})")
    text = value.strip()
    if not text or _UNRESOLVED_REF.search(text):
        return None
    bad = _control_character(text)
    if bad:
        raise MetadataError(f"{source} contains a control character ({bad}); use printable text, like staging")
    if len(text) > ENVIRONMENT_MAX:
        raise MetadataError(f"{source} is {len(text)} characters long; at most {ENVIRONMENT_MAX} are allowed")
    return text


def clean_commit(value: Optional[str], *, source: str = "--commit") -> Optional[str]:
    """A commit sha as given, checked: hex, 4 to 64 characters."""
    text = _text(value)
    if text is None:
        return None
    if not _COMMIT.match(text):
        raise MetadataError(f"{source} {text!r} is not a commit sha (4 to 64 hexadecimal characters)")
    return text


def clean_branch(value: Optional[str], *, source: str = "--branch") -> Optional[str]:
    text = _text(value)
    if text is None:
        return None
    bad = _control_character(text)
    if bad:
        raise MetadataError(f"{source} contains a control character ({bad})")
    if len(text) > BRANCH_MAX:
        raise MetadataError(f"{source} is {len(text)} characters long; at most {BRANCH_MAX} are allowed")
    return text


def clean_pr(value: Optional[int], *, source: str = "--pr") -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MetadataError(f"{source} must be a positive pull or merge request number, not {value!r}")
    return value


# ── Detection ───────────────────────────────────────────────────────────────


def ci_detected(env: Mapping[str, str]) -> bool:
    """True when ``GITHUB_ACTIONS``, ``GITLAB_CI`` or ``CI`` is set (and not ``false``)."""
    return any((env.get(name) or "").strip().lower() not in _FALSY for name in ("GITHUB_ACTIONS", "GITLAB_CI", "CI"))


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def _github_event(env: Mapping[str, str]) -> tuple[Optional[dict], Optional[str]]:
    """``(payload, problem)``: the event file's JSON, or why it could not be read."""
    path = _env(env, "GITHUB_EVENT_PATH")
    if path is None:
        return None, "GITHUB_EVENT_PATH is not set"
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{path} could not be read ({type(e).__name__})"
    if not isinstance(payload, dict):
        return None, f"{path} is not a JSON object"
    return payload, None


def detect_github(env: Mapping[str, str]) -> tuple[Git, Ci, tuple[str, ...]]:
    """GitHub Actions' view of the run. Call only when ``GITHUB_ACTIONS`` is set."""
    warnings: list[str] = []
    github_sha = _env(env, "GITHUB_SHA")
    ref = _env(env, "GITHUB_REF") or ""
    ref_type = _env(env, "GITHUB_REF_TYPE")
    event_name = _env(env, "GITHUB_EVENT_NAME")

    if event_name in ("pull_request", "pull_request_target"):
        event, problem = _github_event(env)
        pull = event.get("pull_request") if event else None
        pull = pull if isinstance(pull, dict) else {}
        head = pull.get("head") if isinstance(pull.get("head"), dict) else {}
        base = pull.get("base") if isinstance(pull.get("base"), dict) else {}

        head_sha = _text(head.get("sha"))
        branch = _env(env, "GITHUB_HEAD_REF") or _text(head.get("ref"))
        base_branch = _text(base.get("ref")) or _env(env, "GITHUB_BASE_REF")
        pr = _int(pull.get("number")) or _int((event or {}).get("number"))
        if pr is None and ref.startswith("refs/pull/"):
            pr = _int(ref.split("/")[2] if len(ref.split("/")) > 2 else None)
        if head_sha is None:
            # Without the event the pull request's head is unknown. GITHUB_SHA
            # is the merge commit, which is what was tested, not the commit the
            # request is about: record it as such rather than as `sha`.
            warnings.append(
                f"GitHub {event_name} event: {problem or 'no pull_request.head.sha in the event file'}; "
                "run.git.sha is unknown and GITHUB_SHA is recorded as run.git.tested_sha. "
                "Pass --commit with the pull request's head to record it"
            )
        git = Git(
            sha=head_sha,
            branch=branch,
            pr=pr,
            base_branch=base_branch,
            tested_sha=github_sha if github_sha and github_sha != head_sha else None,
        )
    else:
        is_tag = ref_type == "tag" or (ref_type is None and ref.startswith("refs/tags/"))
        is_branch = ref_type == "branch" or (ref_type is None and ref.startswith("refs/heads/"))
        branch = _env(env, "GITHUB_REF_NAME") if is_branch and not is_tag else None
        git = Git(sha=github_sha, branch=branch)

    repo = _env(env, "GITHUB_REPOSITORY")
    run_id = _env(env, "GITHUB_RUN_ID")
    url = None
    if repo and run_id:
        server = (_env(env, "GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
        url = f"{server}/{repo}/actions/runs/{run_id}"
        attempt = _env(env, "GITHUB_RUN_ATTEMPT")
        if attempt:
            url += f"/attempts/{attempt}"
    return git, Ci(provider=PROVIDER_GITHUB, url=url, repo=repo), tuple(warnings)


def detect_gitlab(env: Mapping[str, str]) -> tuple[Git, Ci, tuple[str, ...]]:
    """GitLab CI's view of the run. Call only when ``GITLAB_CI`` is set."""
    commit_sha = _env(env, "CI_COMMIT_SHA")
    sha = _env(env, "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA") or commit_sha
    git = Git(
        sha=sha,
        branch=_env(env, "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME") or _env(env, "CI_COMMIT_BRANCH"),
        pr=_int(_env(env, "CI_MERGE_REQUEST_IID")),
        base_branch=_env(env, "CI_MERGE_REQUEST_TARGET_BRANCH_NAME"),
        tested_sha=commit_sha if commit_sha and commit_sha != sha else None,
    )
    return git, Ci(provider=PROVIDER_GITLAB, url=_env(env, "CI_JOB_URL"), repo=_env(env, "CI_PROJECT_PATH")), ()


Runner = Callable[..., Any]


def _git_out(project_root: Path, args: list[str], runner: Runner) -> Optional[str]:
    """A read-only git command's stdout, or None when git or the repository is unavailable.

    Never raises, whatever goes wrong: this is a label on a run, and no answer is
    a fine answer where a crash would cost the run itself.
    """
    try:
        out = runner(["git", *args], cwd=project_root, capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    stdout = getattr(out, "stdout", None)
    if getattr(out, "returncode", 1) != 0 or not isinstance(stdout, str):
        return None
    return stdout


def detect_local_git(project_root: Path, *, dirty: bool = True, runner: Optional[Runner] = None) -> Git:
    """The local repository's HEAD: sha, branch (none when detached), and whether
    tracked files have uncommitted changes (only when *dirty*).

    ``dirty`` ignores untracked files, as ``git describe --dirty`` does: a stray
    file nobody added is not a change to the code under test, and scanning for
    them is the slow half of ``git status`` on a large tree.
    """
    run = runner or subprocess.run
    lines = (_git_out(project_root, ["rev-parse", "HEAD", "--abbrev-ref", "HEAD"], run) or "").split()
    if len(lines) != 2:
        return Git()
    sha, branch = lines
    is_dirty = None
    if dirty:
        status = _git_out(project_root, ["status", "--porcelain", "--untracked-files=no"], run)
        is_dirty = bool(status.strip()) if status is not None else None
    return Git(sha=sha, branch=None if branch == "HEAD" else branch, dirty=is_dirty)


def detect(
    project_root: Path,
    *,
    env: Optional[Mapping[str, str]] = None,
    config_environment: Optional[str] = None,
    dirty: bool = True,
    runner: Optional[Runner] = None,
) -> RunMetadata:
    """Everything the CI, the repository and the environment say, with no flag applied.

    ``config_environment`` is ``config.yaml``'s ``environment:``, already read
    (and checked) by the caller. ``dirty=False`` skips the working-tree check,
    for a caller describing a run after the fact, when the tree is no longer
    the one that ran.
    """
    env = os.environ if env is None else env
    environment = clean_environment(env.get(ENVIRONMENT_VAR), source=ENVIRONMENT_VAR) or clean_environment(
        config_environment, source="config.yaml environment"
    )
    executor = EXECUTOR_CI if ci_detected(env) else EXECUTOR_LOCAL

    ci = None
    warnings: tuple[str, ...] = ()
    if (env.get("GITHUB_ACTIONS") or "").strip().lower() not in _FALSY:
        git, ci, warnings = detect_github(env)
    elif (env.get("GITLAB_CI") or "").strip().lower() not in _FALSY:
        git, ci, warnings = detect_gitlab(env)
    else:
        # Another CI is not detected, so its checkout is only a repository; the
        # working tree is only worth checking on a developer's machine.
        git = detect_local_git(project_root, dirty=dirty and executor == EXECUTOR_LOCAL, runner=runner)

    if ci is not None and git.sha is None and git.tested_sha is None:
        # A CI that names no commit (a variable missing): the checkout does.
        local = detect_local_git(project_root, dirty=False, runner=runner)
        git = replace(git, sha=local.sha)

    return RunMetadata(executor=executor, environment=environment, git=git, ci=ci, warnings=warnings)


# ── Flags ───────────────────────────────────────────────────────────────────


def check_flags(
    *,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    pr: Optional[int] = None,
    environment: Optional[str] = None,
) -> None:
    """Raise :class:`MetadataError` for the first flag value that cannot be used."""
    clean_branch(branch)
    clean_commit(commit)
    clean_pr(pr)
    clean_environment(environment)


def check_environment_variable(env: Optional[Mapping[str, str]] = None) -> None:
    """Raise :class:`MetadataError` when ``STEPPY_ENVIRONMENT`` holds a value that cannot be used."""
    env = os.environ if env is None else env
    clean_environment(env.get(ENVIRONMENT_VAR), source=ENVIRONMENT_VAR)


@dataclass(frozen=True)
class Change:
    """One field a flag changed: what the run said, and what it says now."""

    name: str
    old: Any
    new: Any


def override(
    base: RunMetadata,
    *,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    pr: Optional[int] = None,
    environment: Optional[str] = None,
    executor: Optional[str] = None,
) -> tuple[RunMetadata, list[Change]]:
    """*base* with the flags that were given applied, and what they changed.

    Every value is checked first (:class:`MetadataError`). A ``commit`` naming
    another commit than *base*'s drops ``tested_sha`` and ``dirty``, which
    describe the checkout against the detected commit.
    """
    branch = clean_branch(branch)
    commit = clean_commit(commit)
    pr = clean_pr(pr)
    environment = clean_environment(environment)
    executor = _text(executor)

    git = base.git
    if branch is not None:
        git = replace(git, branch=branch)
    if commit is not None and commit != git.sha:
        git = replace(git, sha=commit, tested_sha=None, dirty=None)
    if pr is not None:
        git = replace(git, pr=pr)

    result = replace(
        base,
        git=git,
        environment=environment if environment is not None else base.environment,
        executor=executor if executor is not None else base.executor,
    )
    changes = [
        Change(name, old, new)
        for name, old, new in (
            ("executor", base.executor, result.executor),
            ("environment", base.environment, result.environment),
            ("git.sha", base.git.sha, result.git.sha),
            ("git.branch", base.git.branch, result.git.branch),
            ("git.pr", base.git.pr, result.git.pr),
            ("git.tested_sha", base.git.tested_sha, result.git.tested_sha),
            ("git.dirty", base.git.dirty, result.git.dirty),
        )
        if old != new
    ]
    return result, changes


def resolve(
    project_root: Path,
    *,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    pr: Optional[int] = None,
    environment: Optional[str] = None,
    executor: Optional[str] = None,
    config_environment: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    dirty: bool = True,
    runner: Optional[Runner] = None,
) -> RunMetadata:
    """Flags, then the CI, then the repository, then nothing: :func:`detect` then :func:`override`.

    The flags are checked before anything is detected, so a bad value fails in
    milliseconds and never after a ``git status``.
    """
    check_flags(branch=branch, commit=commit, pr=pr, environment=environment)
    base = detect(project_root, env=env, config_environment=config_environment, dirty=dirty, runner=runner)
    return override(base, branch=branch, commit=commit, pr=pr, environment=environment, executor=executor)[0]
