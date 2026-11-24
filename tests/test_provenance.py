"""Where a run came from (spec I3): :mod:`steppy.provenance`, one case per context.

Every CI case passes its variables as a dict and a git runner that fails the test
if called, so a value can only have come from the CI. The local cases run real
``git`` in a temporary repository.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from steppy import provenance
from steppy.provenance import Ci, Git, MetadataError, RunMetadata

HEAD = "a" * 40  # the pull or merge request's head
MERGE = "b" * 40  # the merge commit the CI builds to test it
BASE_TIP = "c" * 40  # the base branch's tip (GITHUB_SHA on pull_request_target)


def no_git(*_args, **_kwargs):
    raise AssertionError("git must not run: every value here comes from the CI")


def detect(env: dict, tmp_path: Path, **kwargs) -> RunMetadata:
    return provenance.detect(tmp_path, env=env, runner=no_git, **kwargs)


# ── GitHub Actions ──────────────────────────────────────────────────────────

GITHUB = {
    "CI": "true",
    "GITHUB_ACTIONS": "true",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "acme/shop",
    "GITHUB_RUN_ID": "1234",
    "GITHUB_RUN_ATTEMPT": "2",
}
GITHUB_CI = Ci(
    provider="github-actions", url="https://github.com/acme/shop/actions/runs/1234/attempts/2", repo="acme/shop"
)


def event_file(tmp_path: Path, *, number=42, head_sha=HEAD, head_ref="feature/cart", base_ref="main") -> str:
    path = tmp_path / "event.json"
    payload = {
        "number": number,
        "pull_request": {"number": number, "head": {"sha": head_sha, "ref": head_ref}, "base": {"ref": base_ref}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_github_push_keeps_the_ref_name_and_the_sha(tmp_path):
    env = {
        **GITHUB,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_NAME": "main",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_SHA": HEAD,
    }
    meta = detect(env, tmp_path)
    assert meta.git == Git(sha=HEAD, branch="main")
    assert meta.ci == GITHUB_CI
    assert meta.executor == "ci"
    assert meta.warnings == ()


def test_github_pull_request_records_the_head_not_the_merge_commit(tmp_path):
    """`GITHUB_REF_NAME` is `42/merge` and `GITHUB_SHA` the merge commit there:
    neither is what the pull request is about."""
    env = {
        **GITHUB,
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": event_file(tmp_path),
        "GITHUB_REF": "refs/pull/42/merge",
        "GITHUB_REF_NAME": "42/merge",
        "GITHUB_HEAD_REF": "feature/cart",
        "GITHUB_BASE_REF": "main",
        "GITHUB_SHA": MERGE,
    }
    meta = detect(env, tmp_path)
    assert meta.git == Git(sha=HEAD, branch="feature/cart", pr=42, base_branch="main", tested_sha=MERGE)
    assert meta.ci == GITHUB_CI


def test_github_pull_request_target_reads_the_number_from_the_event(tmp_path):
    """On `pull_request_target`, GITHUB_REF is the base branch and GITHUB_SHA its
    tip: the pull request is only in the event file."""
    env = {
        **GITHUB,
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_EVENT_PATH": event_file(tmp_path, number=7),
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_NAME": "main",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_HEAD_REF": "feature/cart",
        "GITHUB_BASE_REF": "main",
        "GITHUB_SHA": BASE_TIP,
    }
    meta = detect(env, tmp_path)
    assert meta.git == Git(sha=HEAD, branch="feature/cart", pr=7, base_branch="main", tested_sha=BASE_TIP)


def test_github_tag_has_no_branch(tmp_path):
    env = {
        **GITHUB,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/tags/v1.2.0",
        "GITHUB_REF_NAME": "v1.2.0",
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_SHA": HEAD,
    }
    assert detect(env, tmp_path).git == Git(sha=HEAD, branch=None)


def test_github_tag_without_ref_type_is_read_from_the_ref(tmp_path):
    env = {**GITHUB, "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/v1", "GITHUB_REF_NAME": "v1"}
    assert detect(env, tmp_path).git.branch is None


@pytest.mark.parametrize("problem", ["missing", "unset", "not-json", "no-head"])
def test_github_pull_request_without_a_usable_event_file_falls_back_to_the_environment(tmp_path, problem):
    """Never fails: the branch, base and number still come from the environment,
    and GITHUB_SHA is recorded as what it is — the commit tested — not as the
    pull request's commit. A warning says so."""
    env = {
        **GITHUB,
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/42/merge",
        "GITHUB_HEAD_REF": "feature/cart",
        "GITHUB_BASE_REF": "main",
        "GITHUB_SHA": MERGE,
    }
    if problem == "missing":
        env["GITHUB_EVENT_PATH"] = str(tmp_path / "nowhere.json")
    elif problem == "not-json":
        (tmp_path / "event.json").write_text("{not json", encoding="utf-8")
        env["GITHUB_EVENT_PATH"] = str(tmp_path / "event.json")
    elif problem == "no-head":
        (tmp_path / "event.json").write_text(json.dumps({"pull_request": {}}), encoding="utf-8")
        env["GITHUB_EVENT_PATH"] = str(tmp_path / "event.json")

    meta = detect(env, tmp_path)
    assert meta.git == Git(sha=None, branch="feature/cart", pr=42, base_branch="main", tested_sha=MERGE)
    assert len(meta.warnings) == 1
    assert "GITHUB_SHA is recorded as run.git.tested_sha" in meta.warnings[0]
    assert "--commit" in meta.warnings[0]


def test_github_link_without_an_attempt_is_the_run(tmp_path):
    env = {**GITHUB, "GITHUB_RUN_ATTEMPT": "", "GITHUB_SHA": HEAD}
    assert detect(env, tmp_path).ci.url == "https://github.com/acme/shop/actions/runs/1234"


def test_github_enterprise_server_url_is_used(tmp_path):
    env = {**GITHUB, "GITHUB_SERVER_URL": "https://ghe.example.com/", "GITHUB_SHA": HEAD}
    assert detect(env, tmp_path).ci.url.startswith("https://ghe.example.com/acme/shop/actions/runs/1234")


# ── GitLab CI ───────────────────────────────────────────────────────────────

GITLAB = {
    "CI": "true",
    "GITLAB_CI": "true",
    "CI_PROJECT_PATH": "group/sub/shop",
    "CI_JOB_URL": "https://gitlab.example.com/group/sub/shop/-/jobs/77",
}
GITLAB_CI = Ci(provider="gitlab-ci", url="https://gitlab.example.com/group/sub/shop/-/jobs/77", repo="group/sub/shop")


def test_gitlab_branch_pipeline(tmp_path):
    env = {**GITLAB, "CI_COMMIT_BRANCH": "main", "CI_COMMIT_REF_NAME": "main", "CI_COMMIT_SHA": HEAD}
    meta = detect(env, tmp_path)
    assert meta.git == Git(sha=HEAD, branch="main")
    assert meta.ci == GITLAB_CI
    assert meta.executor == "ci"


def test_gitlab_merge_request_pipeline(tmp_path):
    """A plain merge request pipeline runs on the source branch's head; GitLab
    leaves CI_MERGE_REQUEST_SOURCE_BRANCH_SHA empty there, and empty is unset."""
    env = {
        **GITLAB,
        "CI_MERGE_REQUEST_IID": "12",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/cart",
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": "",
        "CI_COMMIT_REF_NAME": "feature/cart",
        "CI_COMMIT_SHA": HEAD,
    }
    assert detect(env, tmp_path).git == Git(sha=HEAD, branch="feature/cart", pr=12, base_branch="main")


def test_gitlab_merged_results_pipeline_records_the_head_and_the_merge(tmp_path):
    env = {
        **GITLAB,
        "CI_MERGE_REQUEST_IID": "12",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/cart",
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": HEAD,
        "CI_COMMIT_REF_NAME": "refs/merge-requests/12/merge",
        "CI_COMMIT_SHA": MERGE,
    }
    assert detect(env, tmp_path).git == Git(
        sha=HEAD, branch="feature/cart", pr=12, base_branch="main", tested_sha=MERGE
    )


def test_gitlab_tag_has_no_branch_and_never_reads_the_ref_name(tmp_path):
    """On a tag, CI_COMMIT_REF_NAME is the tag. Reading it would file a release
    under a branch called v1.2.0."""
    env = {**GITLAB, "CI_COMMIT_TAG": "v1.2.0", "CI_COMMIT_REF_NAME": "v1.2.0", "CI_COMMIT_SHA": HEAD}
    assert detect(env, tmp_path).git == Git(sha=HEAD, branch=None)


def test_gitlab_empty_variables_are_unset(tmp_path):
    env = {**GITLAB, "CI_COMMIT_BRANCH": " ", "CI_MERGE_REQUEST_IID": "", "CI_COMMIT_SHA": HEAD, "CI_JOB_URL": ""}
    meta = detect(env, tmp_path)
    assert meta.git == Git(sha=HEAD)
    assert meta.ci == Ci(provider="gitlab-ci", url=None, repo="group/sub/shop")


# ── Local ───────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "checkout", "-q", "-b", "main")
    (root / "config.yaml").write_text("apps: {}\n", encoding="utf-8")
    _git(root, "add", "config.yaml")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_local_clean_repository(repo):
    meta = provenance.detect(repo, env={})
    assert meta.git == Git(sha=_git(repo, "rev-parse", "HEAD"), branch="main", dirty=False)
    assert meta.executor == "local"
    assert meta.ci is None


def test_local_dirty_repository(repo):
    (repo / "config.yaml").write_text("apps: {edited: true}\n", encoding="utf-8")
    assert provenance.detect(repo, env={}).git.dirty is True


def test_an_untracked_file_is_not_a_dirty_tree(repo):
    """As `git describe --dirty`: a file nobody added is not a change to the code."""
    (repo / "notes.txt").write_text("scratch", encoding="utf-8")
    assert provenance.detect(repo, env={}).git.dirty is False


def test_a_detached_head_has_no_branch(repo):
    _git(repo, "checkout", "-q", "--detach")
    git = provenance.detect(repo, env={}).git
    assert git.sha and git.branch is None


def test_dirty_is_not_checked_when_asked_not_to(repo):
    (repo / "config.yaml").write_text("changed\n", encoding="utf-8")
    assert provenance.detect(repo, env={}, dirty=False).git.dirty is None


def test_no_repository_at_all(tmp_path):
    meta = provenance.detect(tmp_path, env={})
    assert meta.git == Git()
    assert meta.executor == "local"


def test_no_git_installed(tmp_path):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("git")

    assert provenance.detect(tmp_path, env={}, runner=missing).git == Git()


def test_a_fake_subprocess_never_breaks_detection(tmp_path):
    """Test suites swap `subprocess.run` for fakes that return mocks or raise
    TypeError; a label on a run must survive all of them."""
    from unittest.mock import MagicMock

    assert provenance.detect(tmp_path, env={}, runner=lambda *a, **k: MagicMock()).git == Git()
    assert provenance.detect(tmp_path, env={}, runner=lambda *a, **k: dict(None)).git == Git()


def test_another_ci_is_a_ci_run_read_from_the_repository_without_the_dirty_check(repo):
    (repo / "config.yaml").write_text("changed\n", encoding="utf-8")
    meta = provenance.detect(repo, env={"CI": "true"})
    assert meta.executor == "ci"
    assert meta.ci is None
    assert meta.git.sha and meta.git.branch == "main" and meta.git.dirty is None


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, "local"),
        ({"CI": "true"}, "ci"),
        ({"CI": "1"}, "ci"),
        ({"CI": "false"}, "local"),
        ({"CI": ""}, "local"),
        ({"GITHUB_ACTIONS": "true"}, "ci"),
        ({"GITLAB_CI": "true"}, "ci"),
    ],
)
def test_executor_is_ci_when_a_ci_is_detected(tmp_path, env, expected):
    assert provenance.detect(tmp_path, env=env, runner=lambda *a, **k: None).executor == expected


# ── Flags win over each source ──────────────────────────────────────────────


def test_flags_win_over_github(tmp_path):
    env = {
        **GITHUB,
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": event_file(tmp_path),
        "GITHUB_HEAD_REF": "feature/cart",
        "GITHUB_SHA": MERGE,
    }
    meta = provenance.resolve(
        tmp_path, env=env, runner=no_git, branch="release/2", commit="d" * 40, pr=99, executor="local"
    )
    # A commit the user named drops tested_sha: nothing can vouch for what was
    # checked out relative to it. The base branch and the CI job stay.
    assert meta.git == Git(sha="d" * 40, branch="release/2", pr=99, base_branch="main")
    assert meta.executor == "local"
    assert meta.ci == GITHUB_CI


def test_flags_win_over_gitlab(tmp_path):
    env = {
        **GITLAB,
        "CI_MERGE_REQUEST_IID": "12",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/cart",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": HEAD,
        "CI_COMMIT_SHA": MERGE,
    }
    meta = provenance.resolve(tmp_path, env=env, runner=no_git, branch="other", pr=5)
    assert meta.git == Git(sha=HEAD, branch="other", pr=5, tested_sha=MERGE)


def test_flags_win_over_the_local_repository(repo):
    (repo / "config.yaml").write_text("changed\n", encoding="utf-8")
    meta = provenance.resolve(repo, env={}, branch="ci-branch", commit="abc1234")
    assert meta.git == Git(sha="abc1234", branch="ci-branch")  # dirty dropped with the commit


def test_naming_the_detected_commit_keeps_what_describes_it(tmp_path):
    env = {**GITLAB, "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": HEAD, "CI_COMMIT_SHA": MERGE}
    meta = provenance.resolve(tmp_path, env=env, runner=no_git, commit=HEAD)
    assert meta.git.tested_sha == MERGE


def test_override_lists_what_changed():
    base = RunMetadata(executor="ci", environment="staging", git=Git(sha=HEAD, branch="main", tested_sha=MERGE))
    _meta, changes = provenance.override(base, commit="d" * 40, environment="prod", branch="main")
    assert [(c.name, c.old, c.new) for c in changes] == [
        ("environment", "staging", "prod"),
        ("git.sha", HEAD, "d" * 40),
        ("git.tested_sha", MERGE, None),
    ]


# ── The environment ─────────────────────────────────────────────────────────


def test_environment_precedence_flag_then_variable_then_config(tmp_path):
    def env_of(**kwargs):
        return provenance.resolve(tmp_path, runner=lambda *a, **k: None, **kwargs).environment

    assert env_of(env={}, config_environment="staging") == "staging"
    assert env_of(env={"STEPPY_ENVIRONMENT": "prod"}, config_environment="staging") == "prod"
    assert env_of(env={"STEPPY_ENVIRONMENT": "prod"}, config_environment="staging", environment="qa") == "qa"
    # An empty variable is an unset one, so the config still applies.
    assert env_of(env={"STEPPY_ENVIRONMENT": ""}, config_environment="staging") == "staging"
    assert env_of(env={}) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("  staging ", "staging"),
        ("", None),
        ("   ", None),
        (None, None),
        ("eu west", "eu west"),
        ("${DEPLOY_ENV}", None),
        ("é" * 64, "é" * 64),
    ],
)
def test_environment_values_that_are_accepted(value, expected):
    assert provenance.clean_environment(value) == expected


@pytest.mark.parametrize(
    "value, message",
    [
        ("x" * 65, "65 characters long; at most 64"),
        ("staging\n", None),  # stripped, so fine — checked below
        ("stag\ting", "control character (U+0009)"),
        ("stag\x1bing", "control character (U+001B)"),
        ("stag‮ing", "control character (U+202E)"),
        (12, "must be text"),
    ],
)
def test_environment_values_that_are_refused(value, message):
    if message is None:
        assert provenance.clean_environment(value) == "staging"
        return
    with pytest.raises(MetadataError, match=re.escape(message)):
        provenance.clean_environment(value, source="--environment")


def test_a_bad_environment_variable_is_refused_by_name(tmp_path):
    with pytest.raises(MetadataError, match="STEPPY_ENVIRONMENT is 70 characters"):
        provenance.detect(tmp_path, env={"STEPPY_ENVIRONMENT": "y" * 70}, runner=no_git)


@pytest.mark.parametrize("commit", ["main", "abc", "z" * 40, "a" * 65, "abc 123"])
def test_a_commit_flag_must_look_like_a_sha(commit):
    with pytest.raises(MetadataError, match="is not a commit sha"):
        provenance.check_flags(commit=commit)


def test_flags_are_checked_before_anything_is_detected(tmp_path):
    with pytest.raises(MetadataError):
        provenance.resolve(tmp_path, env={}, runner=no_git, branch="a\nb")
    with pytest.raises(MetadataError):
        provenance.resolve(tmp_path, env={}, runner=no_git, pr=0)


# ── The sidecar ─────────────────────────────────────────────────────────────


def test_a_sidecar_without_metadata_is_not_evidence():
    """Before 0.10 the sidecar's executor was always `local`; it says nothing."""
    assert provenance.from_sidecar(None) is None
    assert provenance.from_sidecar({"run_id": "x", "executor": "local", "spec": {}}) is None


def test_recorded_metadata_round_trips_through_the_sidecar(tmp_path):
    from steppy.runmeta import read_run_sidecar, write_run_sidecar

    meta = RunMetadata(
        executor="ci",
        environment="staging",
        git=Git(sha=HEAD, branch="feature/cart", pr=42, base_branch="main", tested_sha=MERGE),
        ci=GITHUB_CI,
        warnings=("a degraded source",),
    )
    write_run_sidecar(tmp_path, run_id="r", spec={}, executor=meta.executor, metadata=meta.sidecar_fields())
    sidecar = read_run_sidecar(tmp_path)
    assert sidecar["environment"] == "staging" and sidecar["ci"]["provider"] == "github-actions"
    assert provenance.from_sidecar(sidecar) == meta


def test_a_run_that_found_nothing_still_records_that_it_looked(tmp_path):
    from steppy.runmeta import read_run_sidecar, write_run_sidecar

    meta = RunMetadata()
    write_run_sidecar(tmp_path, run_id="r", spec={}, metadata=meta.sidecar_fields())
    assert provenance.from_sidecar(read_run_sidecar(tmp_path)) == meta


def test_reading_tolerates_a_newer_or_odder_writer():
    meta = RunMetadata.from_dict(
        {"executor": 3, "environment": ["x"], "git": {"sha": HEAD, "pr": "7", "dirty": "yes", "x": 1}, "ci": {}}
    )
    assert meta == RunMetadata(executor="local", git=Git(sha=HEAD))


def test_describe_is_one_readable_line():
    meta = RunMetadata(
        executor="ci",
        environment="staging",
        git=Git(sha=HEAD, branch="feature/cart", pr=42, base_branch="main"),
        ci=GITHUB_CI,
    )
    assert (
        meta.describe()
        == "git aaaaaaaaaaaa on feature/cart (PR #42 into main) · ci github-actions · environment staging"
    )
