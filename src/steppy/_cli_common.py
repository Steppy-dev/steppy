"""Shared CLI helpers (FLAWS §4.2).

A leaf module — it imports neither ``cli`` nor ``coverage_cli``, so both can
import from it without a circular dependency (the coverage sub-app lives in its
own module but needs the same console / error scaffold).
"""

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from .config import load_config, ConfigError


def _widen_console_encoding() -> None:
    """Stop a non-ASCII character in our own output from killing the command.

    Steppy's summaries use ``→``, ``×`` and ``…`` in a dozen places. On a Windows
    console still running a legacy code page, ``sys.stdout`` is cp1252 and the
    first arrow raises ``UnicodeEncodeError`` from inside Rich — so
    ``steppy coverage merge`` (and every other command with an arrow in it) dies
    at the moment it tries to report success, with a traceback about charmap that
    tells the user nothing about their run.

    Asking for UTF-8 fixes it outright on any modern terminal; ``errors="replace"``
    is the belt and braces for one that genuinely cannot render the glyph, where a
    ``?`` is a fine outcome and a crash is not. Guarded on every axis because this
    runs at import time under pytest capture too, where the stream may be a
    substitute with no ``reconfigure`` at all.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_widen_console_encoding()

console = Console()

# Default wall-clock ceiling (seconds) for the standalone ``steppy seed``
# subprocess. Per-scenario seeds carry their own ``SeedConfig.timeout``; this is
# the fallback for the ad-hoc command that has no scenario context (FLAWS §5.1).
DEFAULT_SEED_TIMEOUT = 120

# Set by the root Typer callback (``--verbose``). Module-level so both cli.py
# and coverage_cli.py observe the same flag.
_VERBOSE = False


def set_verbose(value: bool) -> None:
    """Record the ``--verbose`` flag from the root callback."""
    global _VERBOSE
    _VERBOSE = value


def _debug_enabled() -> bool:
    """True when the user opted into full tracebacks via --verbose or STEPPY_DEBUG."""
    return _VERBOSE or os.environ.get("STEPPY_DEBUG", "") not in ("", "0")


def _error(message: str) -> None:
    """Print a red ERROR line, escaping Rich markup in the dynamic message.

    Scenario content routinely contains square brackets (CSS selectors like
    ``[data-testid="x"]``), which Rich would otherwise parse as style tags and
    silently swallow — mangling the very error the user needs to read.
    """
    console.print(f"[red]ERROR[/red] {escape(message)}")


def _report_unexpected(e: Exception) -> None:
    """Print a friendly one-line error for an unexpected exception.

    The full traceback is shown only when the user opted in (``--verbose`` or
    ``STEPPY_DEBUG=1``). Callers raise ``typer.Exit(1)`` afterwards.
    """
    _error(f"Unexpected error: {e}")
    if _debug_enabled():
        import traceback

        traceback.print_exc()


@contextmanager
def _cli_errors():
    """Shared command error scaffold (FLAWS §4.2).

    Replaces the per-command ``try / except typer.Exit: raise / except
    Exception: _report_unexpected; Exit(1)`` tail that was copy-pasted into
    every command — the exact duplication that let the §2.1 ``typer.Exit``
    bug reach some commands and not others. Re-raises ``typer.Exit`` (so
    intentional exit codes, including the benign ``Exit(0)``, are preserved)
    and turns any other exception into a friendly one-line message.
    """
    try:
        yield
    except typer.Exit:
        raise
    except Exception as e:
        _report_unexpected(e)
        raise typer.Exit(1)


def reject_xdist_args(args) -> None:
    """Reject pytest-xdist flags spliced into the pytest passthrough (FLAWS §5.3).

    The generated ``conftest.py`` files screenshots by locale/layout via mutable
    module globals (``CURRENT_LOCALE``/``CURRENT_LAYOUT``). Those are correct only
    because a pytest process runs tests sequentially. Distributing a single run
    across xdist workers (``steppy run -- -n 4``) is untested and redundant with
    Steppy's own supported parallel path (``steppy run --workers N``), so we block
    it with a clear pointer instead of silently mis-filing captures.
    """
    xdist_flags = ("-n", "--numprocesses", "--dist", "--tx")
    for i, arg in enumerate(args):
        head = arg.split("=", 1)[0]
        # ``-n4`` glues the worker count to the short flag (no space, no ``=``).
        if head in xdist_flags or arg.startswith("-n"):
            _error(
                "pytest-xdist (-n/--numprocesses/--dist) is not supported via the "
                "pytest passthrough - it would mis-file screenshots. Use Steppy's "
                "parallel mode instead: steppy run --workers N"
            )
            raise typer.Exit(1)
        # ``-p xdist`` / ``-p pytest_xdist`` explicitly loads the plugin.
        if arg == "-p" and i + 1 < len(args) and "xdist" in args[i + 1]:
            _error(
                "pytest-xdist is not supported via the pytest passthrough - it "
                "would mis-file screenshots. Use: steppy run --workers N"
            )
            raise typer.Exit(1)


def _load_config_or_exit():
    """Load config, or print a friendly error and exit 1 (FLAWS §4.2).

    Extracts the ``try: load_config() except ConfigError`` block that was
    inlined at the top of most commands. Callers keep their own post-load
    console output.
    """
    try:
        return load_config()
    except ConfigError as e:
        _error(f"Configuration error: {e}")
        raise typer.Exit(1)


_GENERATE_HINT = "\n[yellow]Hint:[/yellow] Run [bold]steppy generate[/bold] first to create test files"


def require_generated_tests(tests_dir: Path, *, require_test_files: bool = True) -> None:
    """Exit 1 unless *tests_dir* holds generated tests.

    The pre-flight shared by ``run`` and ``rerun``: the directory, its
    ``conftest.py``, and (for ``run``) at least one ``test_*.py``. ``rerun``
    skips the last check because pytest's ``--lf`` cache decides what runs.

    Messages are unchanged from the five inlined copies this replaces.
    """
    if not tests_dir.exists():
        console.print(f"[red]ERROR[/red] Tests directory not found: {tests_dir}")
        console.print(_GENERATE_HINT)
        raise typer.Exit(1)

    if not (tests_dir / "conftest.py").exists():
        console.print(f"[red]ERROR[/red] No generated tests found in {tests_dir}")
        console.print(_GENERATE_HINT)
        raise typer.Exit(1)

    if require_test_files and not list(tests_dir.glob("test_*.py")):
        console.print(f"[red]ERROR[/red] No test files found in {tests_dir}")
        console.print(_GENERATE_HINT)
        raise typer.Exit(1)
