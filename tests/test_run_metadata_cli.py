"""Run metadata on the command line (spec I3): `steppy run`, `bundle create`, `bundle upload`.

The detection itself is :mod:`tests.test_provenance`'s; this is the plumbing — the
flags, the order of the checks, the run's sidecar, the manifest a later command
builds, and an archive whose metadata the flags override.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from tests._fake_cloud import GOOD_KEY, FakeCloud
from tests.test_bundle import RUN_ID, build_project
from tests.test_run_upload import RUN_ID as RUN_RUN_ID
from tests.test_run_upload import FakePytest
from typer.testing import CliRunner

from steppy.bundle import create_bundle, inspect_bundle
from steppy.contracts.report import parse_report
from steppy.contracts.upload import REPORT_PATH
from steppy.runmeta import read_run_sidecar, write_run_sidecar

HEAD = "a" * 40
MERGE = "b" * 40

GITLAB_MERGED_RESULTS = {
    "CI": "true",
    "GITLAB_CI": "true",
    "CI_PROJECT_PATH": "group/shop",
    "CI_JOB_URL": "https://gitlab.example.com/group/shop/-/jobs/77",
    "CI_MERGE_REQUEST_IID": "12",
    "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/cart",
    "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
    "CI_MERGE_REQUEST_SOURCE_BRANCH_SHA": HEAD,
    "CI_COMMIT_SHA": MERGE,
}


@pytest.fixture(autouse=True)
def _no_ci_and_no_cloud(monkeypatch) -> None:
    """These tests also run on a CI: its own variables must not leak into them."""
    for name in list(os.environ):
        if name in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "STEPPY_ENVIRONMENT") or name.startswith(("GITHUB_", "CI_")):
            monkeypatch.delenv(name)
    for name in ("STEPPY_API_KEY", "STEPPY_URL", "STEPPY_CLOUD_TOKEN", "STEPPY_CLOUD_URL"):
        monkeypatch.delenv(name, raising=False)


def _set(monkeypatch, env: dict) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def text(result) -> str:
    return " ".join(result.output.split())


# ── steppy run ──────────────────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    tests_dir = tmp_path / "tests" / "e2e"
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_text("# conftest", encoding="utf-8")
    (tests_dir / "test_checkout.py").write_text("# test", encoding="utf-8")
    return tmp_path


def run(*args: str, monkeypatch, cloud: FakeCloud | None = None, code: int = 0):
    from steppy.cli import app

    pytest_ = FakePytest(code)
    if cloud is not None:
        monkeypatch.setattr("steppy.cloud.connect", cloud.connect)
    with (
        patch("steppy.cli.subprocess.run", side_effect=pytest_),
        patch("steppy.cli.new_run_id", return_value=RUN_RUN_ID),
    ):
        result = CliRunner().invoke(app, ["run", *args])
    return result, pytest_


def test_run_records_its_flags_in_the_sidecar(project, monkeypatch):
    result, pytest_ = run(
        "--branch", "release/2", "--commit", "abcdef1", "--pr", "3", "--environment", "staging", monkeypatch=monkeypatch
    )
    assert result.exit_code == 0, result.output
    assert pytest_.calls == 1
    sidecar = read_run_sidecar(project)
    assert sidecar["git"]["branch"] == "release/2"
    assert sidecar["git"]["sha"] == "abcdef1"
    assert sidecar["git"]["pr"] == 3
    assert sidecar["environment"] == "staging"
    assert sidecar["executor"] == "local"
    assert "environment staging" in text(result)


def test_run_in_a_gitlab_merge_request_records_it(project, monkeypatch):
    _set(monkeypatch, GITLAB_MERGED_RESULTS)
    result, _ = run(monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    sidecar = read_run_sidecar(project)
    assert sidecar["executor"] == "ci"
    assert sidecar["git"] == {
        "sha": HEAD,
        "branch": "feature/cart",
        "pr": 12,
        "base_branch": "main",
        "tested_sha": MERGE,
        "dirty": None,
    }
    assert sidecar["ci"] == {
        "provider": "gitlab-ci",
        "url": "https://gitlab.example.com/group/shop/-/jobs/77",
        "repo": "group/shop",
    }


def test_run_upload_sends_the_recorded_metadata(project, monkeypatch):
    """The whole chain: GitLab variables and --environment, recorded by the run,
    read back by the upload, in the manifest sent and in report.json."""
    _set(monkeypatch, {**GITLAB_MERGED_RESULTS, "STEPPY_URL": "https://cloud.test", "STEPPY_API_KEY": GOOD_KEY})
    cloud = FakeCloud()
    result, _ = run("--upload", "--environment", "staging", monkeypatch=monkeypatch, cloud=cloud)
    assert result.exit_code == 0, result.output

    manifest = json.loads(cloud.creates[0].content)["manifest"]
    run_ = manifest["run"]
    assert run_["environment"] == "staging"
    assert run_["executor"] == "ci"
    assert run_["ci"]["provider"] == "gitlab-ci"
    assert run_["git"]["sha"] == HEAD and run_["git"]["tested_sha"] == MERGE and run_["git"]["pr"] == 12

    report_sha = next(f["sha256"] for f in manifest["files"] if f["path"] == REPORT_PATH)
    report = parse_report(json.loads(cloud.blobs[report_sha]))
    assert report.run.environment == "staging"
    assert report.run.ci.url == "https://gitlab.example.com/group/shop/-/jobs/77"


@pytest.mark.parametrize(
    "args, env, expected",
    [
        (["--environment", "x" * 65], {}, "--environment is 65 characters long"),
        (["--environment", "a\tb"], {}, "control character (U+0009)"),
        ([], {"STEPPY_ENVIRONMENT": "y" * 70}, "STEPPY_ENVIRONMENT is 70 characters long"),
        (["--commit", "main"], {}, "--commit 'main' is not a commit sha"),
        (["--branch", "a\nb"], {}, "--branch contains a control character"),
    ],
    ids=["too-long", "control", "variable", "commit", "branch"],
)
def test_a_bad_value_stops_the_run_before_any_test(project, monkeypatch, args, env, expected):
    """And before the upload's key check: a configuration error talks to no server."""
    _set(monkeypatch, {**env, "STEPPY_URL": "https://cloud.test", "STEPPY_API_KEY": GOOD_KEY})
    cloud = FakeCloud()
    result, pytest_ = run("--upload", *args, monkeypatch=monkeypatch, cloud=cloud)
    assert result.exit_code == 1
    assert expected in text(result) and "No test was run" in text(result)
    assert pytest_.calls == 0 and cloud.calls == []
    assert read_run_sidecar(project) is None


def test_a_bad_environment_in_config_stops_the_run(project, monkeypatch):
    (project / "config.yaml").write_text('apps: {}\nenvironment: "stag\\ting"\n', encoding="utf-8")
    result, pytest_ = run(monkeypatch=monkeypatch)
    assert result.exit_code == 1
    assert "config.yaml: environment contains a control character" in text(result)
    assert pytest_.calls == 0


def test_a_config_the_model_rejects_otherwise_still_runs(project, monkeypatch):
    """`steppy run` reads its config best-effort, as before: only the environment
    key is checked strictly."""
    (project / "config.yaml").write_text("apps: {}\nnot_a_key: 1\nenvironment: staging\n", encoding="utf-8")
    result, pytest_ = run(monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert pytest_.calls == 1
    assert read_run_sidecar(project)["environment"] == "staging"


@pytest.mark.parametrize(
    "args, variable, expected",
    [
        ([], None, "from-config"),
        ([], "from-variable", "from-variable"),
        (["--environment", "from-flag"], "from-variable", "from-flag"),
    ],
)
def test_environment_precedence_on_run(project, monkeypatch, args, variable, expected):
    (project / "config.yaml").write_text("apps: {}\nenvironment: from-config\n", encoding="utf-8")
    if variable:
        monkeypatch.setenv("STEPPY_ENVIRONMENT", variable)
    result, _ = run(*args, monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert read_run_sidecar(project)["environment"] == expected


def test_config_environment_resolves_variables(project, monkeypatch):
    (project / "config.yaml").write_text("apps: {}\nenvironment: ${DEPLOY_ENV:-preview}\n", encoding="utf-8")
    run(monkeypatch=monkeypatch)
    assert read_run_sidecar(project)["environment"] == "preview"
    monkeypatch.setenv("DEPLOY_ENV", "prod")
    run(monkeypatch=monkeypatch)
    assert read_run_sidecar(project)["environment"] == "prod"


def test_run_help_lists_the_metadata_flags():
    from steppy.cli import app

    result = CliRunner().invoke(app, ["run", "--help"], env={"COLUMNS": "200"})
    for flag in ("--branch", "--commit", "--pr", "--environment"):
        assert flag in result.output


# ── steppy bundle create ────────────────────────────────────────────────────


@pytest.fixture
def recorded(tmp_path: Path) -> Path:
    """An output tree whose run recorded its metadata, as `steppy run` does now."""
    root = build_project(tmp_path)
    write_run_sidecar(
        root,
        run_id=RUN_ID,
        spec={},
        executor="ci",
        metadata={
            "environment": "staging",
            "git": {"sha": HEAD, "branch": "main", "pr": None, "base_branch": None, "tested_sha": None, "dirty": None},
            "ci": {
                "provider": "github-actions",
                "url": "https://github.com/acme/shop/actions/runs/1",
                "repo": "acme/shop",
            },
        },
    )
    return root


def bundle(*args: str):
    from steppy.cli import app

    return CliRunner().invoke(app, ["bundle", *args], env={"COLUMNS": "200"})


def manifest_of(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_bundle_create_uses_what_the_run_recorded_over_what_it_detects_now(recorded, monkeypatch):
    """A bundle made in a later CI step, or on another machine, describes the run."""
    _set(monkeypatch, GITLAB_MERGED_RESULTS)
    run_ = manifest_of(bundle("create", "--project-root", str(recorded), "--dry-run", "--json"))["run"]
    assert run_["executor"] == "ci"
    assert run_["environment"] == "staging"
    assert run_["git"]["sha"] == HEAD and run_["git"]["branch"] == "main"
    assert run_["ci"]["provider"] == "github-actions"


def test_bundle_create_flags_override_the_recorded_metadata(recorded):
    args = ("create", "--project-root", str(recorded), "--dry-run")
    flags = ("--branch", "hotfix", "--environment", "prod", "--pr", "9", "--executor", "local")
    run_ = manifest_of(bundle(*args, "--json", *flags))["run"]
    assert run_["git"]["branch"] == "hotfix" and run_["git"]["pr"] == 9
    assert run_["environment"] == "prod"
    assert run_["executor"] == "local"
    assert run_["git"]["sha"] == HEAD  # not overridden

    out = text(bundle(*args, *flags))
    assert "Overrode the run's metadata:" in out
    assert "environment staging → prod" in out
    assert "git.branch main → hotfix" in out


def test_the_earlier_flag_names_still_work_and_are_hidden(recorded):
    args = ("create", "--project-root", str(recorded), "--dry-run", "--json")
    run_ = manifest_of(bundle(*args, "--git-branch", "old-name", "--git-sha", "abcdef1"))["run"]
    assert run_["git"]["branch"] == "old-name" and run_["git"]["sha"] == "abcdef1"

    result = bundle(*args, "--git-branch", "one", "--branch", "two")
    assert result.exit_code == 1 and "disagree" in text(result)

    for command in ("create", "upload"):
        help_text = bundle(command, "--help").output
        assert "--branch" in help_text and "--commit" in help_text and "--environment" in help_text
        assert "--git-branch" not in help_text and "--git-sha" not in help_text


def test_bundle_create_detects_a_run_that_recorded_nothing(tmp_path, monkeypatch):
    """A run before 0.10 (its sidecar has no `git`): detected now, from the CI here,
    without the working-tree check, which would describe today's tree."""
    root = build_project(tmp_path)
    _set(monkeypatch, {**GITLAB_MERGED_RESULTS, "STEPPY_ENVIRONMENT": "qa"})
    run_ = manifest_of(bundle("create", "--project-root", str(root), "--dry-run", "--json"))["run"]
    assert run_["executor"] == "ci"
    assert run_["environment"] == "qa"
    assert run_["git"]["sha"] == HEAD and run_["git"]["tested_sha"] == MERGE
    assert run_["git"]["dirty"] is None
    assert run_["ci"]["provider"] == "gitlab-ci"


def test_bundle_create_refuses_a_bad_flag_before_walking(recorded):
    result = bundle("create", "--project-root", str(recorded), "--environment", "z" * 80)
    assert result.exit_code == 1
    assert "--environment is 80 characters long" in text(result)


# ── steppy bundle upload <archive> ──────────────────────────────────────────


@pytest.fixture
def archive(recorded: Path, tmp_path: Path) -> Path:
    return create_bundle(project_root=recorded, target=tmp_path / "run.tar.gz", fmt="gz").path


def upload_archive(project: Path, archive: Path, cloud: FakeCloud, monkeypatch, *args: str):
    from steppy.cli import app

    monkeypatch.setattr("steppy.cloud.connect", cloud.connect)
    return CliRunner().invoke(
        app,
        ["bundle", "upload", str(archive), "--project-root", str(project), "--url", "https://cloud.test"]
        + ["--api-key", GOOD_KEY, *args],
        env={"COLUMNS": "200"},
    )


def test_flags_override_an_archives_metadata_in_the_manifest_sent_and_the_report(recorded, archive, monkeypatch):
    cloud = FakeCloud()
    before = archive.read_bytes()
    result = upload_archive(
        recorded,
        archive,
        cloud,
        monkeypatch,
        "--environment",
        "prod",
        "--branch",
        "release/2",
        "--commit",
        "abcdef1",
        "--run-id",
        "custom-1",
    )
    assert result.exit_code == 0, result.output

    request = cloud.creates[0]
    assert request.headers["Idempotency-Key"] == "custom-1"
    run_ = json.loads(request.content)["manifest"]["run"]
    assert run_["run_id"] == "custom-1"
    assert run_["environment"] == "prod"
    assert run_["git"]["branch"] == "release/2" and run_["git"]["sha"] == "abcdef1"
    assert run_["ci"]["provider"] == "github-actions", "no flag names a CI: the archive's stays"

    # report.json is built after the override, so it says the same.
    files = json.loads(request.content)["manifest"]["files"]
    report_sha = next(f["sha256"] for f in files if f["path"] == REPORT_PATH)
    report = parse_report(json.loads(cloud.blobs[report_sha]))
    assert report.run.run_id == "custom-1"
    assert report.run.environment == "prod"
    assert report.run.git.branch == "release/2"

    out = text(result)
    assert "Overrode the run metadata of run.tar.gz (the archive itself is unchanged):" in out
    assert f"run_id {RUN_ID} → custom-1" in out
    assert "environment staging → prod" in out
    assert "git.sha " + HEAD + " → abcdef1" in out

    # The archive is not rewritten.
    assert archive.read_bytes() == before
    assert inspect_bundle(archive).run.environment == "staging"


def test_an_archive_without_flags_is_sent_as_it_is(recorded, archive, monkeypatch):
    cloud = FakeCloud()
    result = upload_archive(recorded, archive, cloud, monkeypatch)
    assert result.exit_code == 0, result.output
    assert "Overrode" not in result.output
    sent = json.loads(cloud.creates[0].content)["manifest"]["run"]
    assert sent == inspect_bundle(archive).run.model_dump(mode="json")


def test_content_flags_on_an_archive_say_they_change_nothing(recorded, archive, monkeypatch):
    cloud = FakeCloud()
    result = upload_archive(recorded, archive, cloud, monkeypatch, "--no-videos", "--max-size", "1GiB")
    assert result.exit_code == 0, result.output
    assert "--no-videos, --max-size trim what goes into a bundle" in text(result)
    assert "so they change nothing" in text(result)


def test_a_bad_flag_on_an_archive_uploads_nothing(recorded, archive, monkeypatch):
    cloud = FakeCloud()
    result = upload_archive(recorded, archive, cloud, monkeypatch, "--commit", "not-a-sha")
    assert result.exit_code == 1
    assert "is not a commit sha" in text(result)
    assert cloud.calls == []
