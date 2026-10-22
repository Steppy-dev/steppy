"""CLI-surface tests for `steppy record`.

The Playwright-codegen subprocess is patched to write a canned codegen file, so
these exercise the command plumbing — never-clobber, friendly errors, lint
integration, tmp cleanup — without launching a browser.
"""

import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steppy import record_cli
from steppy.cli import app

runner = CliRunner()

CONFIG_YAML = """\
apps:
  admin:
    name: Admin
    url: https://app.example.com
    auth:
      mode: form
      credentials:
        username: "${STEPPY_ADMIN_USERNAME:-admin}"
        password: "${STEPPY_ADMIN_PASSWORD:-secret}"
  editor:
    name: Editor
    url: https://app.example.com/editor
    auth:
      mode: form
      credentials:
        username: "${STEPPY_EDITOR_USERNAME:-editor}"
        password: "${STEPPY_EDITOR_PASSWORD:-secret}"
auth:
  storage_state_dir: ./.auth
"""

SRC_CLICKS = (
    "from playwright.sync_api import Page\n\n\n"
    "def test_example(page: Page) -> None:\n"
    '    page.goto("https://app.example.com/x")\n'
    '    page.get_by_test_id("menu").click()\n'
)
SRC_EMPTY = "from playwright.sync_api import Page\n\n\ndef test_example(page: Page) -> None:\n    pass\n"
SRC_TEXT_SELECTOR = (
    "from playwright.sync_api import Page\n\n\n"
    "def test_example(page: Page) -> None:\n"
    '    page.get_by_text("Delete").click()\n'
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _patch_codegen(monkeypatch, src=None, returncode=0, sink=None, raise_exc=None):
    def _run(argv, *a, **k):
        if raise_exc is not None:
            raise raise_exc
        out = Path(argv[argv.index("-o") + 1])
        if sink is not None:
            sink.append(out)
        if returncode == 0 and src is not None:
            out.write_text(src, encoding="utf-8")
        return types.SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(record_cli.subprocess, "run", _run)


def _norm(output: str) -> str:
    """Collapse rich's soft-wrapping so substring checks are wrap-insensitive."""
    return " ".join(output.split())


# ── Happy path ───────────────────────────────────────────────────────────────


def test_writes_scenario(project, monkeypatch):
    _patch_codegen(monkeypatch, SRC_CLICKS)
    r = runner.invoke(app, ["record", "--role", "admin", "--slug", "flow", "--no-auth"])
    assert r.exit_code == 0, r.output
    md = project / "scenarios" / "flow.md"
    assert md.is_file()
    text = md.read_text("utf-8")
    assert "- action: click" in text
    assert "create" in _norm(r.output)


def test_default_role_is_first_app(project, monkeypatch):
    _patch_codegen(monkeypatch, SRC_CLICKS)
    r = runner.invoke(app, ["record", "--slug", "flow", "--no-auth"])
    assert r.exit_code == 0, r.output
    assert "role: admin" in (project / "scenarios" / "flow.md").read_text("utf-8")


# ── Argument / config errors ─────────────────────────────────────────────────


def test_requires_slug_or_title(project, monkeypatch):
    _patch_codegen(monkeypatch, SRC_CLICKS)
    r = runner.invoke(app, ["record", "--role", "admin", "--no-auth"])
    assert r.exit_code == 1
    assert "slug" in _norm(r.output).lower()


def test_unknown_role(project, monkeypatch):
    _patch_codegen(monkeypatch, SRC_CLICKS)
    r = runner.invoke(app, ["record", "--role", "ghost", "--slug", "x", "--no-auth"])
    assert r.exit_code == 1
    assert "unknown role" in _norm(r.output).lower()


# ── Never-clobber ────────────────────────────────────────────────────────────


def test_refuses_to_overwrite(project, monkeypatch):
    scen = project / "scenarios"
    scen.mkdir()
    (scen / "flow.md").write_text("SENTINEL", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        record_cli.subprocess,
        "run",
        lambda *a, **k: (calls.append(1), types.SimpleNamespace(returncode=0))[1],
    )
    r = runner.invoke(app, ["record", "--role", "admin", "--slug", "flow", "--no-auth"])
    assert r.exit_code == 1
    assert (scen / "flow.md").read_text("utf-8") == "SENTINEL"  # untouched
    assert not calls  # short-circuited before launching codegen


# ── Friendly errors ──────────────────────────────────────────────────────────


def test_codegen_nonzero_exit_is_friendly(project, monkeypatch):
    _patch_codegen(monkeypatch, src=None, returncode=1)
    r = runner.invoke(app, ["record", "--role", "admin", "--slug", "flow", "--no-auth"])
    assert r.exit_code == 1
    assert "playwright install" in _norm(r.output)
    assert not (project / "scenarios" / "flow.md").exists()


def test_playwright_missing_is_friendly(project, monkeypatch):
    _patch_codegen(monkeypatch, raise_exc=FileNotFoundError())
    r = runner.invoke(app, ["record", "--role", "admin", "--slug", "flow", "--no-auth"])
    assert r.exit_code == 1
    assert "playwright" in _norm(r.output).lower()


def test_nothing_recorded_warns_and_writes_nothing(project, monkeypatch):
    _patch_codegen(monkeypatch, SRC_EMPTY)
    r = runner.invoke(app, ["record", "--role", "admin", "--slug", "flow", "--no-auth"])
    assert r.exit_code == 0
    assert "nothing recorded" in _norm(r.output).lower()
    assert not (project / "scenarios" / "flow.md").exists()


# ── Lint integration + cleanup ───────────────────────────────────────────────


def test_lint_warning_is_printed(project, monkeypatch):
    _patch_codegen(monkeypatch, SRC_TEXT_SELECTOR)
    r = runner.invoke(app, ["record", "--role", "admin", "--slug", "flow", "--no-auth"])
    assert r.exit_code == 0
    assert "WARNING" in r.output  # the text= selector is flagged (command still succeeds)


def test_tmp_codegen_file_is_cleaned_up(project, monkeypatch):
    sink = []
    _patch_codegen(monkeypatch, SRC_CLICKS, sink=sink)
    r = runner.invoke(app, ["record", "--role", "admin", "--slug", "flow", "--no-auth"])
    assert r.exit_code == 0
    assert sink and not sink[0].exists()
