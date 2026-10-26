"""Drift guard: Python's ``textStyles`` models vs the npm types package.

Same arrangement and reasons as ``test_seo_config_sync.py``: the block is
declared in ``build_models.py`` (``extra="forbid"``), in
``packages/steppy/index.ts`` and in the hand-maintained ``index.d.ts``. A key
only Python knows lights valid config up red in the author's editor; a key
only the types know type-checks and is then rejected at build time.
"""

import re
from pathlib import Path

import pytest

from steppy.build_models import CodeStyle, EmStyle, LinkStyle, StrongStyle, TextStylesConfig

_PKG = Path(__file__).resolve().parents[1] / "packages" / "steppy"
_TS_FILES = (_PKG / "index.ts", _PKG / "index.d.ts")

_MODELS = {
    "TextStylesConfig": TextStylesConfig,
    "StrongStyle": StrongStyle,
    "EmStyle": EmStyle,
    "CodeStyle": CodeStyle,
    "LinkStyle": LinkStyle,
}


def _ts_keys(path: Path, interface: str) -> set[str]:
    src = path.read_text(encoding="utf-8")
    match = re.search(rf"export interface {interface} \{{(.*?)\n\}}", src, re.DOTALL)
    assert match, f"could not locate `export interface {interface} {{...}}` in {path.name}"
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)
    return set(re.findall(r"^\s*(\w+)\??:", body, re.MULTILINE))


def _py_keys(model) -> set[str]:
    return {(field.alias or name) for name, field in model.model_fields.items()}


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("interface", sorted(_MODELS))
def test_ts_and_python_declare_the_same_keys(path, interface):
    assert _ts_keys(path, interface) == _py_keys(_MODELS[interface]), (
        f"{interface} differs between {path.name} and src/steppy/build_models.py. "
        "Update both packages/steppy/index.ts and index.d.ts (index.d.ts is maintained by hand)."
    )


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
def test_guide_config_declares_text_styles(path):
    assert "textStyles" in _ts_keys(path, "GuideConfig")


def test_em_style_values_agree():
    src = _TS_FILES[0].read_text(encoding="utf-8")
    ts = set(re.findall(r"'(\w+)'", re.search(r"style\?: ([^;]+);", src).group(1)))
    assert ts == set(EmStyle.model_fields["style"].annotation.__args__[0].__args__)
