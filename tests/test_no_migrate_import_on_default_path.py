"""Guard: the default run path must not import steppy.migrate.

Mirrors test_no_bundle_import_on_default_path.py. The codemod parses every
scenario in the project and renders each proposed rewrite through the real
generator; none of that belongs on the path a plain ``steppy run`` — or
``steppy --help`` — takes. Every ``steppy.migrate`` import lives inside a
``migrate_cli`` command body.
"""

import sys
from pathlib import Path

from typer.testing import CliRunner

_MIGRATE_MODULE_PREFIX = "steppy.migrate"


def _purge(prefix: str) -> None:
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + "."):
            del sys.modules[name]


def _loaded(prefix: str) -> list[str]:
    return sorted(name for name in sys.modules if name == prefix or name.startswith(prefix + "."))


def test_steppy_cli_module_load_does_not_import_migrate():
    _purge(_MIGRATE_MODULE_PREFIX)
    sys.modules.pop("steppy.cli", None)
    import steppy.cli  # noqa: F401

    leaked = _loaded(_MIGRATE_MODULE_PREFIX)
    assert not leaked, (
        f"Importing steppy.cli pulled in bundle modules: {leaked}. "
        "Keep all steppy.migrate imports lazy inside command bodies."
    )


def test_migrate_cli_module_load_does_not_import_migrate():
    """``migrate_cli`` itself is imported eagerly by cli.py — its own imports of
    the package must still be deferred to the command bodies."""
    _purge(_MIGRATE_MODULE_PREFIX)
    sys.modules.pop("steppy.migrate_cli", None)
    import steppy.migrate_cli  # noqa: F401

    leaked = _loaded(_MIGRATE_MODULE_PREFIX)
    assert not leaked, f"Importing steppy.migrate_cli pulled in: {leaked}."


def test_run_without_bundle_flag_does_not_import_migrate(tmp_path: Path):
    _purge(_MIGRATE_MODULE_PREFIX)
    from steppy.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["run", "--tests-dir", str(tmp_path / "nonexistent")])
    assert result.exit_code == 1, result.output
    leaked = _loaded(_MIGRATE_MODULE_PREFIX)
    assert not leaked, f"`steppy run` (no --bundle) triggered bundle imports: {leaked}."


def test_cli_load_does_not_import_zstandard_or_tarfile():
    """``zstandard`` is an optional extra and ``tarfile`` is only the bundler's
    business — neither should appear from a bare CLI import."""
    for name in ("zstandard", "tarfile"):
        sys.modules.pop(name, None)
    sys.modules.pop("steppy.cli", None)
    _purge(_MIGRATE_MODULE_PREFIX)
    import steppy.cli  # noqa: F401

    assert "zstandard" not in sys.modules, "steppy.cli pulled in the optional zstandard dependency"
    assert "tarfile" not in sys.modules, "steppy.cli pulled in tarfile"
