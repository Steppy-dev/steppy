"""Shared CLI error handling (FLAWS §2.1, §2.2).

- The intentional ``raise typer.Exit(0)`` on the benign "no scenarios found"
  path must stay a clean exit 0 (it was swallowed by a bare ``except Exception``
  and reprinted as ``ERROR Unexpected error: 0`` + exit 1, because typer.Exit
  subclasses Exception).
- Unexpected exceptions get a friendly one-line message; the full traceback is
  shown only when the user opts in via ``--verbose`` or ``STEPPY_DEBUG=1``.
"""

import traceback as _traceback

from typer.testing import CliRunner

import steppy.cli as cli
import steppy._cli_common as cli_common
from steppy.cli import app

runner = CliRunner()


# ── §2.1: typer.Exit is not swallowed ────────────────────────────────────────


def test_validate_empty_dir_exits_zero(tmp_path):
    empty = tmp_path / "scenarios"
    empty.mkdir()
    result = runner.invoke(app, ["validate", "-d", str(empty)])
    assert result.exit_code == 0, result.output
    assert "No scenario files found" in result.output
    assert "Unexpected error" not in result.output


# ── §2.2: unexpected errors are friendly, not a stack dump ────────────────────


def test_unexpected_error_is_friendly(tmp_path, monkeypatch):
    empty = tmp_path / "scenarios"
    empty.mkdir()

    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "parse_scenarios_from_directory", boom)
    result = runner.invoke(app, ["validate", "-d", str(empty)])
    assert result.exit_code == 1
    assert "Unexpected error: kaboom" in result.output


# ── §2.2: traceback is gated behind --verbose / STEPPY_DEBUG ──────────────────


def _report_and_record(monkeypatch):
    """Call _report_unexpected with print_exc patched to a recorder."""
    calls = []
    monkeypatch.setattr(_traceback, "print_exc", lambda *a, **k: calls.append(1))
    cli._report_unexpected(RuntimeError("boom"))
    return calls


def test_no_traceback_by_default(monkeypatch):
    monkeypatch.setattr(cli_common, "_VERBOSE", False)
    monkeypatch.delenv("STEPPY_DEBUG", raising=False)
    assert _report_and_record(monkeypatch) == []


def test_traceback_when_verbose_flag(monkeypatch):
    monkeypatch.setattr(cli_common, "_VERBOSE", True)
    monkeypatch.delenv("STEPPY_DEBUG", raising=False)
    assert _report_and_record(monkeypatch) == [1]


def test_traceback_when_debug_env(monkeypatch):
    monkeypatch.setattr(cli_common, "_VERBOSE", False)
    monkeypatch.setenv("STEPPY_DEBUG", "1")
    assert _report_and_record(monkeypatch) == [1]


def test_debug_env_zero_is_off(monkeypatch):
    monkeypatch.setattr(cli_common, "_VERBOSE", False)
    monkeypatch.setenv("STEPPY_DEBUG", "0")
    assert _report_and_record(monkeypatch) == []


# ── error messages don't get eaten by Rich markup (selectors have brackets) ───


def test_error_preserves_selector_brackets(tmp_path):
    scn = tmp_path / "scenarios"
    scn.mkdir()
    (scn / "bad.md").write_text(
        '---\ntitle: T\n---\n\n### 1. Click\n- role: editor\n- action: click\n* selector: [data-testid="x"]\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", "-d", str(scn)])
    assert result.exit_code == 1
    # The CSS selector (with square brackets) must survive into the message.
    assert '[data-testid="x"]' in result.output


def test_error_helper_escapes_markup(capsys):
    cli._error('Parse error: selector [data-testid="x"]')
    out = capsys.readouterr().out
    assert '[data-testid="x"]' in out


# ── §4.2: coverage subcommands share the friendly handler ─────────────────────
# Before the refactor they used a bare ``_error(f"{e}")`` tail, so an
# unexpected error printed without the "Unexpected error:" framing and
# --verbose/STEPPY_DEBUG had no effect. They now route through _cli_errors →
# _report_unexpected like every other command.


def test_coverage_report_unexpected_is_friendly(monkeypatch):
    import steppy.coverage_cli as cov

    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cov, "_coverage_settings", boom)
    result = runner.invoke(app, ["coverage", "report"])
    assert result.exit_code == 1, result.output
    assert "Unexpected error: kaboom" in result.output
