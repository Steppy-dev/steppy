"""Guard test for the v0.1 sacred invariant.

The single-worker path (``steppy run`` with no flags or ``--workers 1``)
must NOT import any code from ``steppy.coordinator``, ``steppy.worker``, or
``steppy._parallel``. Lazy imports inside ``cli.run`` enforce this; this
test catches a regression if anyone moves the imports back to module scope.
"""

import sys
from pathlib import Path

from typer.testing import CliRunner


_PARALLEL_MODULE_PREFIXES = (
    "steppy.coordinator",
    "steppy.worker",
    "steppy._parallel",
)


def _purge_parallel_modules() -> None:
    """Drop any cached parallel modules so we can detect re-imports."""
    for name in list(sys.modules):
        if any(name == p or name.startswith(p + ".") for p in _PARALLEL_MODULE_PREFIXES):
            del sys.modules[name]


def _parallel_modules_loaded() -> list[str]:
    return sorted(
        name for name in sys.modules if any(name == p or name.startswith(p + ".") for p in _PARALLEL_MODULE_PREFIXES)
    )


def test_steppy_cli_module_load_does_not_import_parallel():
    """Just importing steppy.cli must not pull in parallel-mode code."""
    _purge_parallel_modules()
    # Force a fresh import of steppy.cli too.
    sys.modules.pop("steppy.cli", None)
    import steppy.cli  # noqa: F401

    leaked = _parallel_modules_loaded()
    assert not leaked, (
        f"Importing steppy.cli pulled in parallel-mode modules: {leaked}. Move these to lazy imports inside cli.run."
    )


def test_run_with_workers_1_does_not_import_parallel(tmp_path: Path):
    """Invoking `steppy run --workers 1` must not import parallel modules,
    even if the run itself fails (e.g., missing tests dir)."""
    _purge_parallel_modules()
    from steppy.cli import app

    runner = CliRunner()
    # We intentionally point at a non-existent tests dir so the command exits
    # quickly with code 1 — we only care about the IMPORT side effect.
    result = runner.invoke(
        app,
        ["run", "--workers", "1", "--tests-dir", str(tmp_path / "nonexistent")],
    )
    # Exit code 1 expected (tests dir missing); we just need to confirm no
    # parallel imports were triggered.
    assert result.exit_code == 1, result.output
    leaked = _parallel_modules_loaded()
    assert not leaked, (
        f"steppy run --workers 1 triggered parallel imports: {leaked}. "
        "The dispatch in cli.run must remain a lazy `from steppy.coordinator "
        "import run_parallel` inside an `if workers >= 2:` block."
    )


def test_run_default_workers_does_not_import_parallel(tmp_path: Path):
    """Same guarantee with no --workers flag at all."""
    _purge_parallel_modules()
    from steppy.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["run", "--tests-dir", str(tmp_path / "nonexistent")],
    )
    assert result.exit_code == 1, result.output
    leaked = _parallel_modules_loaded()
    assert not leaked, f"`steppy run` (no flags) triggered parallel imports: {leaked}."
