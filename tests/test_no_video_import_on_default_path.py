"""Guard: the video/TTS stack stays off the default path.

Fifth member of the negative-import family (parallel, coverage, playwright,
and the two halves of this one). Two separate costs are being kept off every
``steppy`` invocation:

* ``steppy.video`` reaches ``playwright`` and spawns ``ffmpeg``. It is only ever
  wanted behind ``--video``.
* ``steppy.tts.providers.http`` imports ``httpx``, which
  ``test_no_playwright_import_on_default_path.py`` already forbids transitively
  from ``import steppy.cli``. Because ``TtsConfig`` lives in ``models.py`` and
  ``cli.py`` imports that eagerly, the split has to be deliberate: the config
  models and the preset table are pure data, and ``httpx`` is imported inside
  the provider's ``synthesize``.

Runs in a subprocess for the same reason the playwright guard does: these are
ordinary installed packages that other tests legitimately import, so an
in-process ``sys.modules`` check would be defeated by test ordering.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src" / "steppy"

_PROBE = """
import sys
import steppy.cli  # noqa: F401

forbidden = ("steppy.video", "steppy.tts", "httpx", "playwright")
leaked = sorted(
    name
    for name in sys.modules
    if any(name == f or name.startswith(f + ".") for f in forbidden)
)
print(",".join(leaked))
"""

# Importing the config models must stay free of the provider machinery: they are
# on the eager path via cli.py -> config.py -> models.py.
_MODELS_PROBE = """
import sys
import steppy.models  # noqa: F401

forbidden = ("httpx", "steppy.tts", "steppy.video", "playwright")
leaked = sorted(
    name
    for name in sys.modules
    if any(name == f or name.startswith(f + ".") for f in forbidden)
)
print(",".join(leaked))
"""

# The preset table is the seam that makes "swap provider in config" work; it has
# to stay importable without the HTTP client so it can be read from anywhere.
_PRESETS_PROBE = """
import sys
import steppy.tts.presets  # noqa: F401

leaked = sorted(n for n in sys.modules if n == "httpx" or n.startswith("httpx."))
print(",".join(leaked))
"""


def _leaked(probe: str) -> list[str]:
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"probe failed:\n{result.stdout}\n{result.stderr}"
    out = result.stdout.strip()
    return out.split(",") if out else []


def test_cli_import_does_not_pull_in_the_video_stack():
    leaked = _leaked(_PROBE)
    assert not leaked, (
        f"Importing steppy.cli pulled in the video/TTS stack: {leaked}. "
        "Keep `from .video import ...` and `from .tts import ...` inside the "
        "command bodies behind --video; every `steppy` invocation pays for a "
        "module-level import, including `--help`."
    )


def test_config_models_do_not_pull_in_the_provider_machinery():
    leaked = _leaked(_MODELS_PROBE)
    assert not leaked, (
        f"Importing steppy.models pulled in {leaked}. VideoConfig/TtsConfig are "
        "on the eager path (cli -> config -> models), so they must stay plain "
        "pydantic: no imports from steppy.tts.providers, no httpx."
    )


def test_the_preset_table_is_importable_without_an_http_client():
    leaked = _leaked(_PRESETS_PROBE)
    assert not leaked, (
        f"steppy.tts.presets pulled in {leaked}. It is a pure-data registry "
        "(like steppy/presets.py) — keep `import httpx` inside the provider's "
        "synthesize() so the table can be read from anywhere."
    )


@pytest.mark.parametrize("module", ["video.py", "tts/presets.py", "tts/registry.py", "tts/types.py"])
def test_heavy_imports_are_not_at_module_scope(module: str):
    """Pin the mechanism, not just the symptom."""
    path = _SRC / module
    assert path.is_file(), f"missing {path}"
    offenders = [
        (i + 1, line)
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines())
        if line.startswith(("import httpx", "from httpx", "import playwright", "from playwright"))
    ]
    assert not offenders, (
        f"{module} imports httpx/playwright at module scope (lines "
        f"{[n for n, _ in offenders]}). Move them inside the function that needs them, "
        "as steppy/pdf.py does with sync_playwright."
    )
