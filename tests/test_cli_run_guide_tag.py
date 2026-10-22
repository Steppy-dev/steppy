"""Unit tests for composing `steppy run --guide`/`--no-guide` with --tag/--scenario.

The single-worker `run` selection used to hard-block `--guide` + `--tag`/`--scenario`.
It now intersects the guide filter with the tag/scenario filter, matching the
parallel path (coordinator._select_scenario_slugs). These tests assert the
constructed pytest command + env without launching a browser.
"""

import pytest
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from steppy.cli import app

runner = CliRunner()


def _scenario_md(title: str, *, guide: bool, tags: list[str]) -> str:
    lines = [
        "---",
        f"title: {title}",
        f"description: {title}",
    ]
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {t}" for t in tags)
    if guide:
        lines.extend(["guide:", "  enabled: true"])
    lines.extend(
        [
            "---",
            "",
            "### 1. Navigate home",
            "- role: editor",
            "- action: navigate",
            "- target: /",
            "- screenshot: editor",
            "",
        ]
    )
    return "\n".join(lines)


@pytest.fixture
def scenarios_dir(tmp_path):
    """Three scenarios: a guide+billing, a guide+editor, and a plain billing."""
    d = tmp_path / "scenarios"
    d.mkdir(parents=True)
    (d / "billing_guide.md").write_text(
        _scenario_md("Billing Guide", guide=True, tags=["user-guide-billing"]),
        encoding="utf-8",
    )
    (d / "editor_guide.md").write_text(
        _scenario_md("Editor Guide", guide=True, tags=["user-guide-editor"]),
        encoding="utf-8",
    )
    (d / "plain_billing.md").write_text(
        _scenario_md("Plain Billing", guide=False, tags=["user-guide-billing"]),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def tests_dir(tmp_path):
    """A tests/e2e dir holding the generated test files for the three scenarios."""
    d = tmp_path / "tests" / "e2e"
    d.mkdir(parents=True)
    (d / "conftest.py").write_text("# conftest", encoding="utf-8")
    for slug in ("billing_guide", "editor_guide", "plain_billing"):
        (d / f"test_{slug}.py").write_text("# test", encoding="utf-8")
    return d


def _invoke(tests_dir, scenarios_dir, tmp_path, extra_args):
    """Run the CLI with subprocess.run mocked; return (result, mock_run)."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    with (
        patch("steppy.cli.Path.cwd", return_value=tmp_path),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = runner.invoke(
            app,
            [
                "run",
                "--tests-dir",
                str(tests_dir),
                "--scenarios-dir",
                str(scenarios_dir),
                *extra_args,
            ],
        )
    return result, mock_run


def test_guide_tag_intersects(tests_dir, scenarios_dir, tmp_path):
    """--guide --tag selects guide-enabled AND tagged scenarios only."""
    result, mock_run = _invoke(tests_dir, scenarios_dir, tmp_path, ["--guide", "--tag", "user-guide-billing"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert str(tests_dir / "test_billing_guide.py") in cmd
    assert str(tests_dir / "test_editor_guide.py") not in cmd  # wrong tag
    assert str(tests_dir / "test_plain_billing.py") not in cmd  # not guide-enabled

    env = mock_run.call_args[1]["env"]
    assert env.get("STEPPY_GUIDE_MODE") == "1"
    assert env.get("STEPPY_NO_ASSERTS") == "1"
    assert env.get("STEPPY_SCREENSHOTS") == "1"


def test_guide_tag_with_layout(tests_dir, scenarios_dir, tmp_path):
    """The user's exact command: --guide --layouts desktop --tag user-guide-billing."""
    result, mock_run = _invoke(
        tests_dir,
        scenarios_dir,
        tmp_path,
        ["--guide", "--layouts", "desktop", "--tag", "user-guide-billing"],
    )

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert str(tests_dir / "test_billing_guide.py") in cmd
    assert str(tests_dir / "test_editor_guide.py") not in cmd
    assert str(tests_dir / "test_plain_billing.py") not in cmd

    env = mock_run.call_args[1]["env"]
    assert env.get("STEPPY_LAYOUTS") == "desktop"
    assert env.get("STEPPY_GUIDE_MODE") == "1"


def test_guide_scenario_runs_one_in_guide_mode(tests_dir, scenarios_dir, tmp_path):
    """--guide --scenario runs that one scenario with guide mode env."""
    result, mock_run = _invoke(tests_dir, scenarios_dir, tmp_path, ["--guide", "--scenario", "billing_guide"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert str(tests_dir / "test_billing_guide.py") in cmd
    assert str(tests_dir / "test_editor_guide.py") not in cmd

    env = mock_run.call_args[1]["env"]
    assert env.get("STEPPY_GUIDE_MODE") == "1"
    assert env.get("STEPPY_SCREENSHOTS") == "1"


def test_no_guide_tag_excludes_guide_scenarios(tests_dir, scenarios_dir, tmp_path):
    """--no-guide --tag selects only the non-guide tagged scenario."""
    result, mock_run = _invoke(tests_dir, scenarios_dir, tmp_path, ["--no-guide", "--tag", "user-guide-billing"])

    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert str(tests_dir / "test_plain_billing.py") in cmd
    assert str(tests_dir / "test_billing_guide.py") not in cmd  # guide-enabled, excluded

    env = mock_run.call_args[1]["env"]
    assert "STEPPY_GUIDE_MODE" not in env  # --no-guide does not enable guide mode


def test_guide_tag_no_match_exits_zero(tests_dir, scenarios_dir, tmp_path):
    """--guide --tag with no intersection exits 0 without launching pytest."""
    result, mock_run = _invoke(tests_dir, scenarios_dir, tmp_path, ["--guide", "--tag", "does-not-exist"])

    assert result.exit_code == 0
    mock_run.assert_not_called()
    assert "No guide-enabled scenarios found with tag(s)" in result.output


def test_guide_and_no_guide_still_mutually_exclusive(tests_dir, scenarios_dir, tmp_path):
    """--guide and --no-guide remain mutually exclusive with each other."""
    result, mock_run = _invoke(tests_dir, scenarios_dir, tmp_path, ["--guide", "--no-guide"])

    assert result.exit_code == 1
    assert "mutually exclusive" in result.output
    mock_run.assert_not_called()
