"""Guard: importing steppy.cli must not pull in playwright / httpx / markdown_it.

Third guard in the family alongside test_no_parallel_import_on_default_path.py
and test_no_coverage_import_on_default_path.py, but motivated by startup cost
rather than optional-dependency isolation.

``steppy.auth`` imports ``httpx`` + ``playwright.sync_api`` at module scope and
``steppy.guide`` imports ``jinja2`` + ``markdown_it`` (and builds a module-level
``MarkdownIt``). Measured with ``python -X importtime``, that is ~138ms of the
~440ms ``steppy --help`` -- paid by every invocation, including the many
commands that touch none of it. Both are therefore imported inside the command
bodies that need them (``auth login|status|clear``, ``guide``).

Unlike its two sibling guards this runs in a subprocess: playwright and httpx
are ordinary installed packages that other tests legitimately import, so
inspecting the in-process ``sys.modules`` would be defeated by test ordering.
"""

import subprocess
import sys

# Modules whose import cost we refuse to pay on the default path. `jinja2` is
# deliberately absent: `steppy.generator` needs it for `steppy generate`, which
# is core, so it stays eager.
_FORBIDDEN_PREFIXES = ("playwright", "httpx", "markdown_it")

_PROBE = """
import sys
import steppy.cli  # noqa: F401

forbidden = ("playwright", "httpx", "markdown_it")
leaked = sorted(
    name
    for name in sys.modules
    if any(name == f or name.startswith(f + ".") for f in forbidden)
)
print(",".join(leaked))
"""


def _modules_leaked_by_importing_cli() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stdout}\n{result.stderr}"
    out = result.stdout.strip()
    return out.split(",") if out else []


def test_steppy_cli_module_load_does_not_import_playwright_or_httpx():
    leaked = _modules_leaked_by_importing_cli()
    assert not leaked, (
        f"Importing steppy.cli pulled in heavy modules: {leaked}. "
        f"Keep {' / '.join(_FORBIDDEN_PREFIXES)} imports lazy inside the command "
        "bodies that need them -- every `steppy` invocation pays for a "
        "module-level import, including `--help`."
    )


def test_auth_and_guide_are_not_module_level_cli_imports():
    """Pin the mechanism, not just the symptom.

    The leak test above passes if someone drops the import; this one fails if
    someone re-adds it at module scope, with a message pointing at the fix.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "steppy" / "cli.py"
    lines = src.read_text(encoding="utf-8").splitlines()

    # Module scope = zero indentation. A lazy import inside a command body is
    # indented, so this only catches the top-level form.
    offenders = [
        (i + 1, line)
        for i, line in enumerate(lines)
        if line.startswith(
            (
                "from .auth import",
                "from .guide import",
                "from .video import",
                "from .tts import",
                "from .tts.",
                "import steppy.auth",
                "import steppy.guide",
                "import steppy.video",
                "import steppy.tts",
                "from .tours import",
                "from .tours.",
                "import steppy.tours",
            )
        )
    ]
    assert not offenders, (
        f"cli.py has module-level auth/guide/video/tts imports at {[n for n, _ in offenders]}. "
        "Move them inside the command bodies that use them."
    )
