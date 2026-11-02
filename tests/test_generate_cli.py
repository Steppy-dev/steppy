"""``steppy generate``: picking scenarios by slug, the conftest a filtered run
writes, and removing the tests of deleted scenarios."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from steppy.cli import app

runner = CliRunner()


def _scenario(role: str, tags: str = "[]") -> str:
    return f"""---
title: {role} flow
tags: {tags}
---

### 1. Open
- role: {role}
- action: navigate
- target: /
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "editor--flow.md").write_text(_scenario("editor", "[smoke]"), encoding="utf-8")
    (tmp_path / "scenarios" / "reviewer--flow.md").write_text(_scenario("reviewer"), encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "apps:\n"
        "  editor: {name: E, url: 'http://localhost:3000', auth: {mode: bypass}}\n"
        "  reviewer: {name: R, url: 'http://localhost:3000', auth: {mode: bypass}}\n",
        encoding="utf-8",
    )
    return tmp_path


def _generate(*args):
    result = runner.invoke(app, ["generate", "-o", "tests/e2e", *args])
    assert result.exit_code == 0, result.output
    return result


def _e2e(project: Path) -> Path:
    return project / "tests" / "e2e"


def test_scenario_option_generates_only_that_slug(project):
    _generate("-s", "editor--flow")
    assert (_e2e(project) / "test_editor--flow.py").is_file()
    assert not (_e2e(project) / "test_reviewer--flow.py").exists()


def test_scenario_option_is_repeatable(project):
    _generate("-s", "editor--flow", "--scenario", "reviewer--flow")
    assert (_e2e(project) / "test_editor--flow.py").is_file()
    assert (_e2e(project) / "test_reviewer--flow.py").is_file()


def test_an_unknown_slug_is_an_error(project):
    result = runner.invoke(app, ["generate", "-o", "tests/e2e", "-s", "nope"])
    assert result.exit_code == 1
    assert "Scenario not found" in result.output


@pytest.mark.parametrize("args", [["-s", "editor--flow"], ["-t", "smoke"], ["scenarios/editor--flow.md"]])
def test_a_filtered_generate_keeps_every_role_fixture(project, args):
    """The conftest used to be rebuilt from the selection alone, so generating
    one scenario dropped the fixtures every other test needed."""
    _generate()
    _generate(*args, "-f")
    conftest = (_e2e(project) / "conftest.py").read_text(encoding="utf-8")
    assert "def editor_page(" in conftest
    assert "def reviewer_page(" in conftest


def test_a_full_generate_removes_the_test_of_a_deleted_scenario(project):
    _generate()
    (project / "scenarios" / "reviewer--flow.md").unlink()
    result = _generate()
    assert not (_e2e(project) / "test_reviewer--flow.py").exists()
    assert "REMOVED" in result.output and "1 removed" in result.output


def test_a_hand_written_test_is_never_removed(project):
    _generate()
    own = _e2e(project) / "test_my_own.py"
    own.write_text("def test_mine():\n    pass\n", encoding="utf-8")
    _generate()
    assert own.is_file()


def test_a_filtered_generate_removes_nothing(project):
    _generate()
    (project / "scenarios" / "reviewer--flow.md").unlink()
    _generate("-s", "editor--flow")
    assert (_e2e(project) / "test_reviewer--flow.py").is_file()


def test_keep_orphans(project):
    _generate()
    (project / "scenarios" / "reviewer--flow.md").unlink()
    _generate("--keep-orphans")
    assert (_e2e(project) / "test_reviewer--flow.py").is_file()


def test_dry_run_lists_the_orphan_and_keeps_it(project):
    _generate()
    (project / "scenarios" / "reviewer--flow.md").unlink()
    result = _generate("--dry-run")
    assert "REMOVED" in result.output
    assert (_e2e(project) / "test_reviewer--flow.py").is_file()
