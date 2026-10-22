"""Build ids for in-app tours: grammar and resolution.

A tour file carries the id of the app build it was compiled for, and remote
mode only ever fetches the tours of the app's own id (spec fixed decision 8).
``tours.build_id`` in ``config.yaml`` accepts:

``git-sha``
    The first 12 hex digits of ``HEAD`` of the repository holding the project
    root. Fixed length on purpose: ``git rev-parse --short`` grows with the repo.
``git-sha:<repo-path>``
    The same, for a repository elsewhere — when the scenarios live next to,
    not inside, the app repository.
a literal
    ``^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$`` — a release number, a
    CI variable through ``${APP_BUILD_ID:-git-sha}``. The id becomes a path
    segment (``tours/<id>/``), so ``..`` and separators are unrepresentable.

An unresolved ``${VAR}`` is accepted when the config loads (``steppy run``
must keep working where the app-build variable is unset) and rejected when an
id is actually needed. No dirty-tree suffix: two runs over the same tree must
produce identical bytes.

A stdlib-only leaf.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

BUILD_ID_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$"
GIT_SHA_PREFIX_LEN = 12
GIT_SHA = "git-sha"

_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class BuildIdError(ValueError):
    """A ``tours.build_id`` value that is malformed or cannot be resolved."""


@dataclass(frozen=True)
class BuildIdSpec:
    kind: Literal["git", "literal"]
    repo: Optional[str] = None
    value: Optional[str] = None


def is_build_id(value: object) -> bool:
    # Windows reserves a device name before *any* extension: `con.v2` is as
    # un-creatable a directory as `con`.
    return (
        isinstance(value, str)
        and re.fullmatch(BUILD_ID_PATTERN, value) is not None
        and value.split(".", 1)[0].casefold() not in _WINDOWS_RESERVED
    )


def parse_build_id_spec(raw: object) -> BuildIdSpec:
    """Parse a ``tours.build_id`` value; raise :class:`BuildIdError` on a bad one."""
    if not isinstance(raw, str) or not raw.strip():
        raise BuildIdError("tours.build_id must be a non-empty string (git-sha, git-sha:<repo-path> or a literal)")
    text = raw.strip()
    if "${" in text:
        raise BuildIdError(
            f"tours.build_id {text!r} references an environment variable that is not set; "
            "give it a default, e.g. ${APP_BUILD_ID:-git-sha}"
        )
    if text == GIT_SHA:
        return BuildIdSpec(kind="git")
    if text.startswith(GIT_SHA + ":"):
        repo = text[len(GIT_SHA) + 1 :].strip()
        if not repo:
            raise BuildIdError("tours.build_id 'git-sha:' needs a repository path after the colon")
        return BuildIdSpec(kind="git", repo=repo)
    if not is_build_id(text):
        raise BuildIdError(
            f"tours.build_id {text!r} is not a valid build id: use git-sha, git-sha:<repo-path>, "
            f"or a literal matching {BUILD_ID_PATTERN} (it becomes a directory name)"
        )
    return BuildIdSpec(kind="literal", value=text)


def check_build_id_syntax(raw: object) -> str:
    """Load-time check: let an unresolved ``${…}`` through, validate everything else."""
    if isinstance(raw, str) and "${" in raw:
        return raw
    parse_build_id_spec(raw)
    return raw  # type: ignore[return-value]


def _git_head(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildIdError(
            f"tours.build_id: cannot run git in {repo} ({exc}); set a literal build id or pass --build-id"
        ) from exc
    sha = out.stdout.strip().lower()
    if out.returncode != 0 or not _SHA_RE.fullmatch(sha):
        detail = out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "not a git repository"
        raise BuildIdError(
            f"tours.build_id: git rev-parse HEAD failed in {repo} ({detail}); "
            "set a literal build id, ${APP_BUILD_ID:-git-sha}, or pass --build-id"
        )
    return sha


def resolve_build_id(raw: object, project_root: Path, *, override: Optional[str] = None) -> str:
    """Resolve the build id to the value written into tour files.

    ``override`` (``steppy tours --build-id``) wins over the configured value
    and goes through the same grammar.
    """
    spec = parse_build_id_spec(override if override else raw)
    if spec.kind == "literal":
        assert spec.value is not None
        return spec.value
    repo = Path(project_root)
    if spec.repo:
        repo = (repo / spec.repo).resolve()
    return _git_head(repo)[:GIT_SHA_PREFIX_LEN]
