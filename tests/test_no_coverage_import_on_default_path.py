"""Guard: the default run path must not import steppy.coverage.

Mirrors test_no_parallel_import_on_default_path.py. Coverage code (sqlite
store, sourcemap decoder, httpx-using manifest loader) is lazy-imported only
from ``--coverage`` post-run merge and the ``steppy coverage`` commands; a
plain ``steppy run`` must never load it.
"""

import sys
from pathlib import Path

from typer.testing import CliRunner

_COVERAGE_MODULE_PREFIX = "steppy.coverage"


def _purge_coverage_modules() -> None:
    for name in list(sys.modules):
        if name == _COVERAGE_MODULE_PREFIX or name.startswith(_COVERAGE_MODULE_PREFIX + "."):
            del sys.modules[name]


def _coverage_modules_loaded() -> list[str]:
    return sorted(
        name
        for name in sys.modules
        if name == _COVERAGE_MODULE_PREFIX or name.startswith(_COVERAGE_MODULE_PREFIX + ".")
    )


def test_steppy_cli_module_load_does_not_import_coverage():
    _purge_coverage_modules()
    sys.modules.pop("steppy.cli", None)
    import steppy.cli  # noqa: F401

    leaked = _coverage_modules_loaded()
    assert not leaked, (
        f"Importing steppy.cli pulled in coverage modules: {leaked}. "
        "Keep all steppy.coverage imports lazy inside command bodies."
    )


def test_run_without_coverage_flag_does_not_import_coverage(tmp_path: Path):
    _purge_coverage_modules()
    from steppy.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["run", "--tests-dir", str(tmp_path / "nonexistent")])
    assert result.exit_code == 1, result.output
    leaked = _coverage_modules_loaded()
    assert not leaked, f"`steppy run` (no --coverage) triggered coverage imports: {leaked}."
