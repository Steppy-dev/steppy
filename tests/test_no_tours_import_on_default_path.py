"""Guard: the default paths must not import the in-app tours compiler.

Mirrors ``test_no_migrate_import_on_default_path.py``. ``steppy.tours`` (and
the producer contract ``steppy.contracts.tours``) are only reached behind
``tours.enabled`` — by ``generate``/``validate``/``build`` — and by the
``steppy tours`` command body. ``tours_cli`` is imported eagerly to register
the command, so its own imports of the package must stay deferred.
"""

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

_PREFIXES = ("steppy.tours", "steppy.contracts.tours")


def _purge() -> None:
    for name in list(sys.modules):
        if any(name == p or name.startswith(p + ".") for p in _PREFIXES):
            del sys.modules[name]


def _loaded() -> list[str]:
    return sorted(n for n in sys.modules if any(n == p or n.startswith(p + ".") for p in _PREFIXES))


def test_steppy_cli_module_load_does_not_import_tours():
    _purge()
    sys.modules.pop("steppy.cli", None)
    import steppy.cli  # noqa: F401

    assert not _loaded(), f"Importing steppy.cli pulled in {_loaded()}; keep steppy.tours imports lazy."


def test_tours_cli_module_load_does_not_import_tours():
    _purge()
    sys.modules.pop("steppy.tours_cli", None)
    import steppy.tours_cli  # noqa: F401

    assert not _loaded(), f"Importing steppy.tours_cli pulled in {_loaded()}."


def test_run_does_not_import_tours(tmp_path: Path):
    _purge()
    from steppy.cli import app

    result = CliRunner().invoke(app, ["run", "--tests-dir", str(tmp_path / "nonexistent")])
    assert result.exit_code == 1, result.output
    assert not _loaded(), f"`steppy run` triggered tours imports: {_loaded()}"


def test_generate_and_validate_without_tours_do_not_import_tours(tmp_path, monkeypatch):
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "config.yaml").write_text(
        "apps:\n  editor: {name: E, url: 'http://localhost:3000', auth: {mode: bypass}}\nlocales: [en]\n"
        "default_locale: en\n",
        encoding="utf-8",
    )
    (tmp_path / "scenarios" / "editor--x.md").write_text(
        "---\ntitle: X\n---\n\n### 1. Open\n- role: editor\n- action: navigate\n- target: /\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    _purge()
    from steppy.cli import app

    for args in (["validate"], ["generate", "-o", "tests/e2e"]):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output
    assert not _loaded(), f"tours-free generate/validate imported {_loaded()}"


def test_the_compiler_pulls_in_no_heavy_dependency():
    probe = (
        "import sys, steppy.tours.pipeline, steppy.tours.replay\n"
        "bad = [m for m in ('markdown_it', 'playwright', 'httpx') if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"steppy.tours imports {out.stdout.strip()}"
