"""Python ↔ TypeScript drift guard for the in-app tour contract.

The compiler (Python) and the overlay (TypeScript) must agree on every name,
enum, pattern and limit, or a tour the compiler emits is rejected in the
browser with ``invalid-data``. Both sides keep their values as plain literals
so this test can compare them without running Node.
"""

import re
from pathlib import Path

import pytest

from steppy import buildid, naming, tour_spec

PLAYER = Path(__file__).resolve().parents[1] / "packages" / "player" / "src"
NAMES_TS = PLAYER / "ui" / "names.ts"
CONTRACT_TS = PLAYER / "tour" / "contract.ts"


def _ts_consts(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    out: dict[str, object] = {}
    for name, value in re.findall(r"^export const ([A-Z0-9_]+) = '([^']*)';$", text, re.M):
        out[name] = value
    for name, value in re.findall(r"^export const ([A-Z0-9_]+) = (\d+);$", text, re.M):
        out[name] = int(value)
    for name, body in re.findall(r"^export const ([A-Z0-9_]+) = \[([^\]]*)\] as const;$", text, re.M):
        out[name] = tuple(re.findall(r"'([^']*)'", body))
    return out


NAMES = _ts_consts(NAMES_TS)
CONTRACT = _ts_consts(CONTRACT_TS)


@pytest.mark.parametrize(
    "name",
    [
        "PRODUCT",
        "TOUR_HOST_TAG",
        "TOURS_MANIFEST_KIND",
        "TOUR_FILE_KIND",
        "TOURS_MANIFEST_FILENAME",
        "TOURS_KILL_SWITCH_GLOBAL",
        "TOUR_RESUME_STORAGE_KEY",
    ],
)
def test_product_names_match(name):
    assert NAMES.get(name) == getattr(naming, name), f"{NAMES_TS.name}: {name} drifted from naming.py"


@pytest.mark.parametrize(
    "name",
    [
        "TOUR_SCHEMA_VERSION",
        "ADVANCE_KINDS",
        "PLACEMENTS",
        "TARGET_CLICK_MODES",
        "TOUR_EVENTS",
        "TOUR_ERROR_REASONS",
        "CHROME_STRING_LOCALES",
        "TOUR_PARTS",
        "ATTR_TOUR_ID",
        "ATTR_STEP_ID",
        "ATTR_STATE",
        "ATTR_ADVANCE",
        "BUBBLE_STATES",
        "TEST_ID_PATTERN",
        "STEP_ID_PATTERN",
        "TEST_ID_ATTRIBUTE_PATTERN",
        "TOUR_ID_PATTERN",
        "LAYOUT_NAME_PATTERN",
        "ROLE_PATTERN",
        "LOCALE_PATTERN",
        "SRI_PATTERN",
        "TOUR_FILE_NAME_PATTERN",
        "ROUTE_PATTERN",
        "MEDIA_QUERY_PATTERN",
        "MAX_TOURS",
        "MAX_STEPS",
        "MAX_LOCALES",
        "MAX_LAYOUTS",
        "MAX_TITLE_CHARS",
        "MAX_TEXT_CHARS",
        "MAX_ROUTE_CHARS",
        "MAX_URL_CHARS",
        "MAX_MEDIA_QUERY_CHARS",
        "MAX_TOUR_FILE_BYTES",
        "MAX_MANIFEST_BYTES",
    ],
)
def test_contract_values_match(name):
    assert CONTRACT.get(name) == getattr(tour_spec, name), f"{CONTRACT_TS.name}: {name} drifted from tour_spec.py"


def test_targeted_kinds_and_build_id_match():
    assert set(CONTRACT["TARGETED_ADVANCE_KINDS"]) == tour_spec.TARGETED_ADVANCE_KINDS
    assert CONTRACT["BUILD_ID_PATTERN"] == buildid.BUILD_ID_PATTERN


def test_ts_patterns_carry_no_backslashes():
    # A backslash in a TS string literal is an escape, not a regex escape: the
    # two languages would then compile different patterns from one value.
    for path in (NAMES_TS, CONTRACT_TS):
        assert "\\" not in path.read_text(encoding="utf-8"), path.name
