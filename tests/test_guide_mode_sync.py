"""Drift guard: Python's GuideMode vs the npm types package and MODE_LABELS.

A guide's rendering modes are declared in four places, in two languages:

  * ``GuideMode``    -- src/steppy/build_models.py           (the enum)
  * ``MODE_LABELS``  -- src/steppy/build.py                  (display names)
  * ``GuideEntry.modes`` -- packages/steppy/index.ts         (authoring types)
  * ``GuideEntry.modes`` -- packages/steppy/index.d.ts       (hand-maintained sibling)

Two distinct failure modes make this worth pinning:

1. **A mode without a ``MODE_LABELS`` row is a hard crash, far from its cause.**
   ``templates/angular/component.ts.j2`` renders ``mode_labels[mode]`` while
   looping over a guide's declared modes, so the miss surfaces as a Jinja
   ``UndefinedError`` during ``steppy build``, naming a template rather than the
   enum that actually drifted.

2. **A mode missing from the TS types is silent.** ``defineGuideConfig`` is the
   only thing type-checking a target app's ``steppy.guide.config.ts``; a mode
   Python accepts but the types reject makes the author's editor red on
   perfectly valid config, and — because ``index.d.ts`` is maintained by hand
   next to ``index.ts`` — the two can also drift from each other.

Mirrors the house pattern in tests/test_action_enum_sync.py.
"""

import re
from pathlib import Path

import pytest

from steppy.build import MODE_LABELS
from steppy.build_models import GuideMode

_PKG = Path(__file__).resolve().parents[1] / "packages" / "steppy"
_INDEX_TS = _PKG / "index.ts"
_INDEX_DTS = _PKG / "index.d.ts"

_TS_FILES = (_INDEX_TS, _INDEX_DTS)

_REBUILD_HINT = (
    "Update the `modes?:` union in BOTH packages/steppy/index.ts and "
    "packages/steppy/index.d.ts (index.d.ts is maintained by hand, not generated)."
)


def _parse_modes_union(path: Path) -> set[str]:
    """Extract the member list of `modes?: ('a' | 'b')[]`."""
    src = path.read_text(encoding="utf-8")
    match = re.search(r"modes\?:\s*\((.*?)\)\[\]", src, re.DOTALL)
    assert match, f"could not locate `modes?: (...)[]` in {path.name}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _python_modes() -> set[str]:
    return {m.value for m in GuideMode}


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
def test_ts_types_file_is_present(path: Path):
    assert path.is_file(), f"missing {path}"


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
def test_every_python_mode_is_declared_in_the_ts_types(path: Path):
    """The direction that makes a valid config light up red in the author's editor."""
    missing = sorted(_python_modes() - _parse_modes_union(path))
    assert not missing, f"GuideMode members absent from {path.name}: {missing}.\n{_REBUILD_HINT}"


@pytest.mark.parametrize("path", _TS_FILES, ids=lambda p: p.name)
def test_ts_types_declare_no_unknown_modes(path: Path):
    """The reverse -- a mode the Python build would reject at config load."""
    extra = sorted(_parse_modes_union(path) - _python_modes())
    assert not extra, (
        f"{path.name} declares modes Python does not accept: {extra}. "
        "Either add them to GuideMode in src/steppy/build_models.py or remove them."
    )


def test_the_two_ts_files_agree():
    """index.d.ts is hand-maintained beside index.ts -- pin them together."""
    assert _parse_modes_union(_INDEX_TS) == _parse_modes_union(_INDEX_DTS), _REBUILD_HINT


def test_every_mode_has_a_display_label():
    """A missing row surfaces as a Jinja UndefinedError, not as an enum error."""
    missing = sorted(_python_modes() - set(MODE_LABELS))
    assert not missing, (
        f"GuideMode members with no MODE_LABELS row: {missing}.\n"
        "Add one to MODE_LABELS in src/steppy/build.py. Without it, any Angular "
        "guide declaring the mode fails to build with a Jinja UndefinedError from "
        "templates/angular/component.ts.j2, which names the template rather than "
        "the enum that drifted."
    )


def test_mode_labels_lists_no_unknown_modes():
    extra = sorted(set(MODE_LABELS) - _python_modes())
    assert not extra, f"MODE_LABELS references modes absent from GuideMode: {extra}. Remove the stale rows."


@pytest.mark.parametrize("mode", sorted(_python_modes()))
def test_each_mode_name_is_a_plain_slug(mode: str):
    """Guards the regex above: quoting/format assumptions stay valid."""
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", mode), mode
