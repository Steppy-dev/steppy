"""Guard: the default run path must not import steppy.report.

Mirrors test_no_bundle_import_on_default_path.py. The reporter builds a Jinja
environment, reads tar archives and walks a whole ``output/`` tree; none of that
belongs on the path a plain ``steppy run`` — or ``steppy --help`` — takes. Every
``steppy.report`` import lives inside the ``report_cli`` command body.

The second test is the one that actually bites: ``cli.py`` imports ``report_cli``
*eagerly* in order to register the command, so only deferring the imports inside
the body keeps this honest.
"""

import sys
from pathlib import Path

from typer.testing import CliRunner

_REPORT_MODULE_PREFIX = "steppy.report"


def _purge(prefix: str) -> None:
    for name in [m for m in sys.modules if m == prefix or m.startswith(prefix + ".")]:
        del sys.modules[name]


def _loaded(prefix: str) -> list[str]:
    return sorted(m for m in sys.modules if m == prefix or m.startswith(prefix + "."))


def test_steppy_cli_module_load_does_not_import_report():
    _purge(_REPORT_MODULE_PREFIX)
    sys.modules.pop("steppy.cli", None)

    import steppy.cli  # noqa: F401

    leaked = _loaded(_REPORT_MODULE_PREFIX)
    assert not leaked, (
        f"`import steppy.cli` pulled in {leaked}. Keep all steppy.report imports lazy inside command bodies."
    )


def test_report_cli_module_load_does_not_import_report():
    """``report_cli`` is imported eagerly by cli.py — its own imports of the
    package must still be deferred to the command body."""
    _purge(_REPORT_MODULE_PREFIX)
    sys.modules.pop("steppy.report_cli", None)

    import steppy.report_cli  # noqa: F401

    leaked = _loaded(_REPORT_MODULE_PREFIX)
    assert not leaked, f"`import steppy.report_cli` pulled in {leaked}."


def test_run_without_a_report_does_not_import_report(tmp_path: Path):
    _purge(_REPORT_MODULE_PREFIX)
    from steppy.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["run", "--tests-dir", str(tmp_path / "nonexistent")])
    assert result.exit_code == 1, result.output
    leaked = _loaded(_REPORT_MODULE_PREFIX)
    assert not leaked, f"`steppy run` triggered report imports: {leaked}."


def test_cli_load_does_not_import_tarfile_or_zstandard():
    """The reporter reads archives, but ``steppy --help`` must not pay for that.
    Also re-asserts what the bundle guard already claims, because a new eagerly
    imported CLI module is exactly how that regresses."""
    _purge(_REPORT_MODULE_PREFIX)
    _purge("steppy.bundle")
    for name in ("tarfile", "zstandard"):
        sys.modules.pop(name, None)
    sys.modules.pop("steppy.cli", None)

    import steppy.cli  # noqa: F401

    for name in ("tarfile", "zstandard"):
        assert name not in sys.modules, f"`import steppy.cli` pulled in {name}."
