"""Unit tests for the rerun CLI command."""

import json
import pytest
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from steppy.cli import app

runner = CliRunner()


@pytest.fixture
def fake_tests_dir(tmp_path):
    """Create a minimal tests/e2e directory structure."""
    tests_dir = tmp_path / "tests" / "e2e"
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_text("# conftest", encoding="utf-8")
    (tests_dir / "test_example.py").write_text("# test", encoding="utf-8")
    return tests_dir


@pytest.fixture
def cache_with_failures(tmp_path):
    """Create a .pytest_cache with one failed test."""
    cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
    cache_dir.mkdir(parents=True)
    lastfailed = {"tests/e2e/test_example.py::test_example": True}
    (cache_dir / "lastfailed").write_text(json.dumps(lastfailed), encoding="utf-8")
    return cache_dir / "lastfailed"


@pytest.fixture
def cache_empty(tmp_path):
    """Create a .pytest_cache with no failures."""
    cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "lastfailed").write_text("{}", encoding="utf-8")
    return cache_dir / "lastfailed"


def test_rerun_no_tests_dir():
    """rerun exits 1 when tests directory does not exist."""
    result = runner.invoke(app, ["rerun", "--tests-dir", "/nonexistent/path"])
    assert result.exit_code == 1
    assert "Tests directory not found" in result.output


def test_rerun_no_conftest(tmp_path):
    """rerun exits 1 when conftest.py is missing."""
    tests_dir = tmp_path / "tests" / "e2e"
    tests_dir.mkdir(parents=True)
    result = runner.invoke(app, ["rerun", "--tests-dir", str(tests_dir)])
    assert result.exit_code == 1
    assert "No generated tests found" in result.output


def test_rerun_no_cache(fake_tests_dir, tmp_path):
    """rerun exits 0 with helpful message when no pytest cache exists."""
    with patch("steppy.cli.Path.cwd", return_value=tmp_path):
        result = runner.invoke(app, ["rerun", "--tests-dir", str(fake_tests_dir)])
    assert result.exit_code == 0
    assert "No previous test run found" in result.output


def test_rerun_all_passed(fake_tests_dir, cache_empty, tmp_path):
    """rerun exits 0 when last run had no failures."""
    with patch("steppy.cli.Path.cwd", return_value=tmp_path):
        result = runner.invoke(app, ["rerun", "--tests-dir", str(fake_tests_dir)])
    assert result.exit_code == 0
    assert "No failed tests to re-run" in result.output


def test_rerun_with_failures(fake_tests_dir, cache_with_failures, tmp_path):
    """rerun invokes pytest with --lf when there are failures."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch("steppy.cli.Path.cwd", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = runner.invoke(app, ["rerun", "--tests-dir", str(fake_tests_dir)])

    assert result.exit_code == 0
    assert "Re-running 1 previously failed test(s)" in result.output
    assert "test_example.py::test_example" in result.output

    # Verify pytest was called with --lf
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert "--lf" in cmd
    assert "--browser" in cmd
    assert "chromium" in cmd


def test_rerun_with_file_level_failures(fake_tests_dir, tmp_path):
    """A parallel run writes FILE-LEVEL lastfailed keys (no `::`, one per
    failed scenario file). Verify `rerun` reads them and drives `pytest --lf`
    the same way — this is the shape the parallel coordinator now produces."""
    cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "lastfailed").write_text(json.dumps({"tests/e2e/test_example.py": True}), encoding="utf-8")

    mock_result = MagicMock()
    mock_result.returncode = 0
    with (
        patch("steppy.cli.Path.cwd", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = runner.invoke(app, ["rerun", "--tests-dir", str(fake_tests_dir)])

    assert result.exit_code == 0
    assert "Re-running 1 previously failed test(s)" in result.output
    assert "tests/e2e/test_example.py" in result.output
    cmd = mock_run.call_args[0][0]
    assert "--lf" in cmd


def test_rerun_headed(fake_tests_dir, cache_with_failures, tmp_path):
    """rerun --headed sets HEADED env var."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch("steppy.cli.Path.cwd", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = runner.invoke(app, ["rerun", "--tests-dir", str(fake_tests_dir), "--headed"])

    assert result.exit_code == 0
    call_args = mock_run.call_args
    env = call_args[1]["env"]
    assert env.get("HEADED") == "1"


def test_rerun_screenshots(fake_tests_dir, cache_with_failures, tmp_path):
    """rerun --screenshots sets STEPPY_SCREENSHOTS env var."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch("steppy.cli.Path.cwd", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = runner.invoke(app, ["rerun", "--tests-dir", str(fake_tests_dir), "--screenshots"])

    assert result.exit_code == 0
    call_args = mock_run.call_args
    env = call_args[1]["env"]
    assert env.get("STEPPY_SCREENSHOTS") == "1"


def test_rerun_passthrough_args(fake_tests_dir, cache_with_failures, tmp_path):
    """rerun passes extra args through to pytest."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch("steppy.cli.Path.cwd", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = runner.invoke(app, ["rerun", "--tests-dir", str(fake_tests_dir), "--", "-v", "--tb=short"])

    assert result.exit_code == 0
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert "-v" in cmd
    assert "--tb=short" in cmd


def test_rerun_plain_env_unchanged(fake_tests_dir, cache_with_failures, tmp_path):
    """Regression guard: plain `rerun` (no --guide) sets no guide env vars."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch("steppy.cli.Path.cwd", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = runner.invoke(app, ["rerun", "--tests-dir", str(fake_tests_dir)])

    assert result.exit_code == 0
    env = mock_run.call_args[1]["env"]
    steppy_keys = {k for k in env if k.startswith("STEPPY_")}
    # No --screenshots, no --guide → zero STEPPY_ vars (matches pre-feature behavior).
    assert steppy_keys == set(), f"unexpected STEPPY_ env vars on plain rerun: {steppy_keys}"
    cmd = mock_run.call_args[0][0]
    assert "--lf" in cmd
    assert str(fake_tests_dir) in cmd


# ── Guide rerun fixtures + tests ─────────────────────────────────────────────


@pytest.fixture
def guide_scenarios_dir(tmp_path):
    """A scenarios dir with one guide-enabled scenario (slug 'guide_demo')."""
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir(parents=True)
    (scenarios_dir / "guide_demo.md").write_text(
        "---\n"
        "title: Guide Demo\n"
        "description: A guide-enabled scenario\n"
        "guide:\n"
        "  enabled: true\n"
        "---\n\n"
        "### 1. Navigate home\n"
        "- role: editor\n"
        "- action: navigate\n"
        "- target: /\n"
        "- screenshot: editor\n",
        encoding="utf-8",
    )
    return scenarios_dir


@pytest.fixture
def guide_tests_dir(tmp_path):
    """A tests/e2e dir holding the generated test file for 'guide_demo'."""
    tests_dir = tmp_path / "tests" / "e2e"
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_text("# conftest", encoding="utf-8")
    (tests_dir / "test_guide_demo.py").write_text("# test", encoding="utf-8")
    return tests_dir


def test_rerun_guide_sets_guide_env(guide_tests_dir, guide_scenarios_dir, cache_with_failures, tmp_path):
    """rerun --guide reconstructs the guide env and passes --lf scoped to guide files."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch("steppy.cli.Path.cwd", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = runner.invoke(
            app,
            [
                "rerun",
                "--tests-dir",
                str(guide_tests_dir),
                "--scenarios-dir",
                str(guide_scenarios_dir),
                "--guide",
            ],
        )

    assert result.exit_code == 0
    env = mock_run.call_args[1]["env"]
    # --guide auto-enables guide mode, no-asserts, and screenshots.
    assert env.get("STEPPY_GUIDE_MODE") == "1"
    assert env.get("STEPPY_NO_ASSERTS") == "1"
    assert env.get("STEPPY_SCREENSHOTS") == "1"

    cmd = mock_run.call_args[0][0]
    assert "--lf" in cmd
    # --lf is scoped to the guide scenario's test file, not the whole dir.
    assert str(guide_tests_dir / "test_guide_demo.py") in cmd
    assert str(guide_tests_dir) not in cmd


def test_rerun_guide_locales_layouts(guide_tests_dir, guide_scenarios_dir, cache_with_failures, tmp_path):
    """rerun --guide --locales/--layouts sets the parametrization env vars."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch("steppy.cli.Path.cwd", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = runner.invoke(
            app,
            [
                "rerun",
                "--tests-dir",
                str(guide_tests_dir),
                "--scenarios-dir",
                str(guide_scenarios_dir),
                "--guide",
                "--locales",
                "fr,nl",
                "--layouts",
                "desktop,mobile",
            ],
        )

    assert result.exit_code == 0
    env = mock_run.call_args[1]["env"]
    assert env.get("STEPPY_LOCALES") == "fr,nl"
    assert env.get("STEPPY_LAYOUTS") == "desktop,mobile"
