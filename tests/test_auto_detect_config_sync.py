"""Drift guard: Python's AutoDetectConfig vs the npm types package.

Same two failure modes as tests/test_seo_config_sync.py: a key Python accepts
but the TS types omit turns a valid config red in the author's editor, and a
key the TS types declare but Python rejects type-checks, ships, and then fails
the build on ``extra = "forbid"``. ``index.d.ts`` is hand-maintained beside
``index.ts``, so both are checked.
"""

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from steppy.build_models import AutoDetectConfig, AutoDetectStorageKeys

_PKG = Path(__file__).resolve().parents[1] / "packages" / "steppy"
_TS_FILES = (_PKG / "index.ts", _PKG / "index.d.ts")

_MODELS: dict[str, type[BaseModel]] = {
    "AutoDetectConfig": AutoDetectConfig,
    "AutoDetectStorageKeys": AutoDetectStorageKeys,
}


def _interface_keys(path: Path, name: str) -> set[str]:
    src = path.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", src, re.DOTALL)
    assert match, f"could not locate `export interface {name} {{...}}` in {path.name}"
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)
    body = re.sub(r"//[^\n]*", "", body)
    return set(re.findall(r"^\s*(\w+)\??:", body, re.MULTILINE))


def _python_keys(model: type[BaseModel]) -> set[str]:
    return {(field.alias or name) for name, field in model.model_fields.items()}


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("name", sorted(_MODELS))
def test_ts_interface_matches_python_model(path: Path, name: str):
    ts = _interface_keys(path, name)
    py = _python_keys(_MODELS[name])
    assert ts == py, (
        f"{name} in {path.name} declares {sorted(ts)}, Python accepts {sorted(py)}. "
        "Update the interface in BOTH packages/steppy/index.ts and index.d.ts, or the model."
    )


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
def test_guide_config_exposes_auto_detect(path: Path):
    assert "autoDetect" in _interface_keys(path, "GuideConfig")
