"""``steppy run --upload`` / ``--require-upload`` (spec I1, I6; decision D6).

pytest itself is replaced by a fake ``subprocess.run`` that records one failing
test the way a generated suite would (an event file in ``STEPPY_EVENTS_DIR``),
and the cloud by :class:`tests._fake_cloud.FakeCloud`, swapped in for
``steppy.cloud.connect``. Everything between the two is the real command.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from tests._fake_cloud import GOOD_KEY, FakeCloud
from typer.testing import CliRunner

RUN_ID = "20260101T000000Z-abcdef"
#: ``steppy.cli.subprocess`` is the ``subprocess`` module, so patching its
#: ``run`` patches every caller — git detection included. Those go through.
_REAL_RUN = subprocess.run
NODEID = "tests/e2e/test_checkout.py::test_flow"


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A generated suite to run, in the working directory: every run writes its
    records under ``./output/``, which must not land in the checkout."""
    monkeypatch.chdir(tmp_path)
    for name in ("STEPPY_API_KEY", "STEPPY_URL", "STEPPY_CLOUD_TOKEN", "STEPPY_CLOUD_URL"):
        monkeypatch.delenv(name, raising=False)
    tests_dir = tmp_path / "tests" / "e2e"
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_text("# conftest", encoding="utf-8")
    (tests_dir / "test_checkout.py").write_text("# test", encoding="utf-8")
    return tmp_path


@pytest.fixture
def configured(monkeypatch) -> None:
    monkeypatch.setenv("STEPPY_URL", "https://cloud.test")
    monkeypatch.setenv("STEPPY_API_KEY", GOOD_KEY)


class FakePytest:
    """Stands in for the pytest subprocess: records a test, returns *code*."""

    def __init__(self, code: int) -> None:
        self.code = code
        self.calls = 0

    def __call__(self, cmd, env=None, cwd=None, **kwargs):
        if list(cmd[1:3]) != ["-m", "pytest"]:
            return _REAL_RUN(cmd, env=env, cwd=cwd, **kwargs)
        self.calls += 1
        events = Path(env["STEPPY_EVENTS_DIR"])
        events.mkdir(parents=True, exist_ok=True)
        outcome = "failed" if self.code else "passed"
        rows = [
            {"v": 1, "kind": "test-start", "ts": 1.0, "nodeid": NODEID, "scenario": "checkout"},
            {"v": 1, "kind": "test-end", "ts": 2.0, "nodeid": NODEID, "scenario": "checkout", "outcome": outcome},
        ]
        (events / "checkout__default-default.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        shot = Path(cwd) / "output" / "screenshots" / "checkout" / "step-01.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        shot.write_bytes(b"png")
        result = MagicMock()
        result.returncode = self.code
        return result


def run(cloud: FakeCloud, *args: str, code: int = 0, monkeypatch) -> tuple[object, FakePytest]:
    from steppy.cli import app

    pytest_ = FakePytest(code)
    monkeypatch.setattr("steppy.cloud.connect", cloud.connect)
    with (
        patch("steppy.cli.subprocess.run", side_effect=pytest_),
        patch("steppy.cli.new_run_id", return_value=RUN_ID),
    ):
        result = CliRunner().invoke(app, ["run", *args])
    return result, pytest_


def text(result) -> str:
    """The output with Rich's line wrapping undone."""
    return " ".join(result.output.split())


# --- the start check ----------------------------------------------------------


def test_no_key_stops_the_command_before_any_test(project, monkeypatch) -> None:
    monkeypatch.setenv("STEPPY_URL", "https://cloud.test")
    cloud = FakeCloud()
    result, pytest_ = run(cloud, "--upload", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    assert "no API key" in text(result) and "STEPPY_API_KEY" in text(result) and "No test was run" in text(result)
    assert pytest_.calls == 0 and cloud.calls == []


def test_no_address_stops_the_command_before_any_test(project, monkeypatch) -> None:
    monkeypatch.setenv("STEPPY_API_KEY", GOOD_KEY)
    cloud = FakeCloud()
    result, pytest_ = run(cloud, "--upload", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    assert "no Steppy Cloud address" in text(result) and "--url" not in text(result)
    assert pytest_.calls == 0 and cloud.calls == []


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_stops_the_command_before_any_test(project, configured, monkeypatch, status) -> None:
    cloud = FakeCloud(key_status=status)
    result, pytest_ = run(cloud, "--upload", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    assert "refused the API key from STEPPY_API_KEY" in text(result)
    assert pytest_.calls == 0
    assert cloud.calls == ["GET /api/v1/key"], "a refused key is asked once, never retried"


def test_an_unreachable_server_only_warns_and_the_tests_run(project, configured, monkeypatch) -> None:
    cloud = FakeCloud(unreachable=True)
    result, pytest_ = run(cloud, "--upload", code=0, monkeypatch=monkeypatch)
    assert pytest_.calls == 1
    assert result.exit_code == 0, result.output
    assert "could not check the API key" in text(result)
    assert "upload failed" in text(result)


def test_the_start_check_prints_the_project(project, configured, monkeypatch) -> None:
    cloud = FakeCloud()
    result, _ = run(cloud, "--upload", monkeypatch=monkeypatch)
    assert "Upload to Shop (https://cloud.test/p/shop)" in text(result)
    assert cloud.calls[0] == "GET /api/v1/key"


# --- the upload after the tests -------------------------------------------------


def test_a_failed_run_is_uploaded_and_keeps_its_exit_code(project, configured, monkeypatch) -> None:
    cloud = FakeCloud()
    result, pytest_ = run(cloud, "--upload", code=1, monkeypatch=monkeypatch)
    assert pytest_.calls == 1
    assert result.exit_code == 1
    assert cloud.calls == ["GET /api/v1/key", "POST /api/v1/runs", "POST /api/v1/runs/R1/finalize"]
    assert cloud.finalized == {"R1": 1}
    assert result.output.rstrip().endswith("https://cloud.test/r/R1"), "the run's URL is the last line"
    manifest = json.loads(cloud.creates[0].content)["manifest"]
    assert manifest["run"]["run_id"] == RUN_ID
    assert cloud.creates[0].headers["Idempotency-Key"] == RUN_ID
    assert [t["outcome"] for t in manifest["tests"]] == ["failed"]
    assert "report.json" in {f["path"] for f in manifest["files"]}


def test_a_passing_run_is_uploaded(project, configured, monkeypatch) -> None:
    cloud = FakeCloud()
    result, _ = run(cloud, "--upload", code=0, monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert "Status passed" in text(result)


class _CreateFails(FakeCloud):
    """The key check passes, then the server falls over."""

    def _create(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down for maintenance"})


@pytest.mark.parametrize("code", [0, 1])
def test_an_upload_failure_keeps_the_tests_exit_code(project, configured, monkeypatch, code) -> None:
    cloud = _CreateFails()
    result, _ = run(cloud, "--upload", code=code, monkeypatch=monkeypatch)
    assert result.exit_code == code
    assert "upload failed" in text(result) and "down for maintenance" in text(result)
    assert "result is unchanged" in text(result)
    assert cloud.calls.count("POST /api/v1/runs") == 3, "retried twice, then given up"


def test_an_unexpected_error_in_the_upload_is_a_warning_too(project, configured, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("a bug in the uploader")

    monkeypatch.setattr("steppy.cloud.attach_report", boom)
    result, _ = run(FakeCloud(), "--upload", code=0, monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "a bug in the uploader" in text(result)


@pytest.mark.parametrize("code,expected", [(0, 1), (1, 1)])
def test_require_upload_makes_an_upload_failure_fail_the_command(
    project, configured, monkeypatch, code, expected
) -> None:
    result, _ = run(_CreateFails(), "--require-upload", code=code, monkeypatch=monkeypatch)
    assert result.exit_code == expected
    assert "--require-upload makes that fail the command" in text(result)


def test_require_upload_implies_upload(project, configured, monkeypatch) -> None:
    cloud = FakeCloud()
    result, _ = run(cloud, "--require-upload", code=0, monkeypatch=monkeypatch)
    assert result.exit_code == 0, result.output
    assert cloud.finalized == {"R1": 1}


def test_without_the_flag_nothing_is_uploaded_even_with_a_key(project, configured, monkeypatch) -> None:
    """D6: a key in the environment is never enough on its own."""

    class Refuses(FakeCloud):
        def connect(self, endpoint, **kwargs):
            raise AssertionError("steppy run without --upload contacted the cloud")

    cloud = Refuses()
    result, pytest_ = run(cloud, code=1, monkeypatch=monkeypatch)
    assert pytest_.calls == 1 and result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert cloud.calls == [] and cloud.puts == []
    assert "Upload" not in result.output


def test_a_parallel_run_is_uploaded_too(project, configured, monkeypatch) -> None:
    def run_parallel(**kwargs):
        Path("output").mkdir(exist_ok=True)
        return 1

    monkeypatch.setattr("steppy.coordinator.run_parallel", run_parallel)
    cloud = FakeCloud()
    result, _ = run(cloud, "--upload", "--workers", "2", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    assert cloud.finalized == {"R1": 1}
