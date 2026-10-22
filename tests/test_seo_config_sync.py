"""Drift guard: Python's SeoConfig vs the npm types package.

The `seo` block is declared in three places, in two languages:

  * ``SeoConfig``  -- src/steppy/build_models.py      (the model, extra="forbid")
  * ``SeoConfig``  -- packages/steppy/index.ts        (authoring types)
  * ``SeoConfig``  -- packages/steppy/index.d.ts      (hand-maintained sibling)

Two failure modes make this worth pinning, and they point opposite ways:

1. **A key in Python but not in the TS types is silent and wrong-way-round.**
   ``defineGuideConfig`` is the only thing type-checking a target app's
   ``steppy.guide.config.ts``, so the author's editor goes red on config the
   build accepts -- and because ``index.d.ts`` is maintained by hand next to
   ``index.ts``, the two can drift from each other as well.
2. **A key in the TS types but not in Python is worse**: it type-checks in the
   editor, ships, and is then rejected at build time by ``extra = "forbid"``.

Scoped to ``SeoConfig`` on purpose. A guard over every build-config interface
would fail today on pre-existing drift (``TemplateMode``, ``GuideEntry.template``,
``BuildConfig.defaultTemplate`` and ``BuildConfig.localeLabels`` are all absent
from ``index.d.ts``), and a test that is red on arrival protects nothing.

Mirrors the house pattern in tests/test_guide_mode_sync.py.
"""

import re
from pathlib import Path

import pytest

from steppy.build_models import SeoConfig

_PKG = Path(__file__).resolve().parents[1] / "packages" / "steppy"
_INDEX_TS = _PKG / "index.ts"
_INDEX_DTS = _PKG / "index.d.ts"

_TS_FILES = (_INDEX_TS, _INDEX_DTS)

_REBUILD_HINT = (
    "Update the `SeoConfig` interface in BOTH packages/steppy/index.ts and "
    "packages/steppy/index.d.ts (index.d.ts is maintained by hand, not generated)."
)


def _parse_seo_keys(path: Path) -> set[str]:
    """Extract the property names of `export interface SeoConfig { ... }`."""
    src = path.read_text(encoding="utf-8")
    match = re.search(r"export interface SeoConfig \{(.*?)\n\}", src, re.DOTALL)
    assert match, f"could not locate `export interface SeoConfig {{...}}` in {path.name}"
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)
    body = re.sub(r"//[^\n]*", "", body)
    return set(re.findall(r"^\s*(\w+)\??:", body, re.MULTILINE))


def _python_seo_keys() -> set[str]:
    """The camelCase names a config file actually writes."""
    return {(field.alias or name) for name, field in SeoConfig.model_fields.items()}


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
def test_ts_types_file_is_present(path: Path):
    assert path.is_file(), f"missing {path}"


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
def test_every_python_key_is_declared_in_the_ts_types(path: Path):
    """The direction that makes valid config light up red in the author's editor."""
    missing = sorted(_python_seo_keys() - _parse_seo_keys(path))
    assert not missing, f"SeoConfig fields absent from {path.name}: {missing}.\n{_REBUILD_HINT}"


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
def test_ts_types_declare_no_unknown_keys(path: Path):
    """The reverse -- a key that type-checks, ships, then fails at build time
    because SeoConfig sets `extra = "forbid"`."""
    extra = sorted(_parse_seo_keys(path) - _python_seo_keys())
    assert not extra, (
        f"{path.name} declares SeoConfig keys Python does not accept: {extra}. "
        "Either add them to SeoConfig in src/steppy/build_models.py or remove them."
    )


def test_the_two_ts_files_agree():
    """index.d.ts is hand-maintained beside index.ts -- pin them together."""
    assert _parse_seo_keys(_INDEX_TS) == _parse_seo_keys(_INDEX_DTS), _REBUILD_HINT


@pytest.mark.parametrize("key", sorted(_python_seo_keys()))
def test_each_key_is_a_plain_camel_case_name(key: str):
    """Guards the regexes above: quoting/format assumptions stay valid."""
    assert re.fullmatch(r"[a-z][A-Za-z0-9]*", key), key
