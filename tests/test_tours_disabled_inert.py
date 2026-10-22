"""With in-app tours off, nothing new is produced (spec M1).

Byte identity of the generated tests and conftest is proven against the
pre-feature templates by ``tests/_codegen_snapshot.py``; these checks pin the
CLI-level half: no ``tour_plan`` means the very same file, no tours block means
no ``_steppy_tours.py`` and no ``output/tours``, and ``tours.enabled: false``
behaves exactly like an absent block.
"""

from pathlib import Path

from typer.testing import CliRunner

from steppy.cli import app
from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.parser import parse_scenarios_from_directory

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
SCENARIO = """---
title: Create an invoice
tour: true
---

### 1. Open
- role: editor
- action: navigate
- target: /invoices

### 2. New
- role: editor
- action: click
- selector: '[data-testid="new-invoice-btn"]'
"""


def _project(tmp_path: Path, tours_block: str) -> Path:
    (tmp_path / "scenarios").mkdir(parents=True)
    (tmp_path / "scenarios" / "editor--create-invoice.md").write_text(SCENARIO, encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "apps:\n  editor: {name: E, url: 'http://localhost:3000', auth: {mode: bypass}}\n"
        "locales: [en]\ndefault_locale: en\n" + tours_block,
        encoding="utf-8",
    )
    return tmp_path


def _generate(project: Path, monkeypatch) -> dict[str, bytes]:
    monkeypatch.chdir(project)
    result = CliRunner().invoke(app, ["generate", "-o", "tests/e2e"])
    assert result.exit_code == 0, result.output
    return {
        p.relative_to(project).as_posix(): p.read_bytes()
        for p in project.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_an_absent_block_and_a_disabled_block_generate_the_same_tree(tmp_path, monkeypatch):
    absent = _generate(_project(tmp_path / "a", ""), monkeypatch)
    disabled = _generate(_project(tmp_path / "b", "tours:\n  enabled: false\n"), monkeypatch)
    absent.pop("config.yaml")
    disabled.pop("config.yaml")
    assert absent == disabled
    assert not any(name.startswith("output/tours") for name in absent)
    assert "tests/e2e/_steppy_tours.py" not in absent
    assert b"__tour" not in absent["tests/e2e/test_editor--create-invoice.py"]


def test_a_disabled_project_warns_about_scenarios_that_ask_for_tours(tmp_path, monkeypatch):
    project = _project(tmp_path, "tours:\n  enabled: false\n")
    monkeypatch.chdir(project)
    result = CliRunner().invoke(app, ["generate", "-o", "tests/e2e"])
    assert "in-app tours are off" in result.output


def test_no_tour_plan_renders_the_plain_test(tmp_path):
    project = _project(tmp_path, "")
    config = load_config(project / "config.yaml")
    generator = SteppyGenerator(config, templates_dir=TEMPLATES)
    (scenario,) = parse_scenarios_from_directory(project / "scenarios")
    assert generator.generate_test_file(scenario) == generator.generate_test_file(scenario, tour_plan=None)
    assert "_steppy_tours" not in generator.generate_test_file(scenario)
    assert "_tours_with_kill_switch" not in generator.generate_conftest([scenario])
