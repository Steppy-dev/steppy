"""Golden in-app tour data, shared with the player's vitest suite.

``contracts/fixtures/tours/valid/`` is the compiler's output for
``fixtures/tour-demo/project`` (build id ``demo-0001``), committed byte for
byte. ``packages/player/test/tour/golden.test.ts`` loads the same files with
the overlay's validator, and both suites run every entry of
``contracts/fixtures/tours/cases.json`` — a mutation of a valid file plus the
outcome each side must reach. That is the cross-language guard: a field the
compiler starts emitting, or a rule either side tightens, fails one of them.

Regenerate the goldens after an intended compiler change with::

    STEPPY_REGEN_TOUR_GOLDEN=1 python -m pytest tests/test_tours_golden.py
"""

import copy
import json
import os
from pathlib import Path

import pytest

from steppy.config import load_config
from steppy.contracts.tours import (
    TourDataInvalid,
    TourSchemaUnsupported,
    check_consistency,
    parse_tour_file,
    parse_tours_manifest,
    sri_sha384,
)
from steppy.parser import parse_scenarios_from_directory
from steppy.tours.compiler import compile_tours
from steppy.tours.emit import render_artifacts
from steppy.tours.settings import ToursSettings

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures" / "tour-demo" / "project"
GOLDEN = ROOT / "contracts" / "fixtures" / "tours"
VALID = GOLDEN / "valid"
BUILD_ID = "demo-0001"


def _compiled() -> dict[str, bytes]:
    config = load_config(PROJECT / "config.yaml")
    settings = ToursSettings.from_config(config, PROJECT)
    result = compile_tours(parse_scenarios_from_directory(PROJECT / "scenarios"), settings)
    assert not result.has_errors, [d.render() for d in result.diagnostics if d.severity == "error"]
    return render_artifacts(result, settings, BUILD_ID).all_files()


def test_the_golden_tree_is_the_compiler_output():
    files = _compiled()
    if os.environ.get("STEPPY_REGEN_TOUR_GOLDEN") == "1":
        VALID.mkdir(parents=True, exist_ok=True)
        for old in VALID.glob("*.json"):
            old.unlink()
        for name, data in files.items():
            (VALID / name).write_bytes(data)
    on_disk = {p.name: p.read_bytes() for p in VALID.glob("*.json")}
    assert on_disk == files, "contracts/fixtures/tours/valid/ is stale; regenerate it (see this module's docstring)"


def test_the_golden_tree_passes_the_contract():
    manifest = parse_tours_manifest(json.loads((VALID / "tours.manifest.json").read_bytes()))
    files = {p.name: p.read_bytes() for p in VALID.glob("*.json") if p.name != "tours.manifest.json"}
    for data in files.values():
        parse_tour_file(json.loads(data))
        assert b"\r" not in data
    assert check_consistency(manifest, files) == []
    for tour in manifest.tours:
        for locale, name in tour.files.items():
            assert tour.integrity[locale] == sri_sha384(files[name])


def _patch(doc, path: str, value=None, *, delete=False):
    keys = path.split(".")
    node = doc
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    last = keys[-1]
    if isinstance(node, list):
        if delete:
            node.pop(int(last))
        else:
            node[int(last)] = value
    elif delete:
        node.pop(last, None)
    else:
        node[last] = value


CASES = json.loads((GOLDEN / "cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_python_side_of_every_case(case):
    raw = case.get("raw")
    if raw is None:
        doc = copy.deepcopy(json.loads((VALID / case["base"]).read_bytes()))
        for path, value in (case.get("set") or {}).items():
            _patch(doc, path, value)
        for path in case.get("delete") or []:
            _patch(doc, path, delete=True)
    parse = parse_tours_manifest if case["kind"] == "manifest" else parse_tour_file
    if case["python"] == "accept":
        assert raw is None
        parse(doc)
        return
    with pytest.raises((TourDataInvalid, TourSchemaUnsupported, ValueError)) as info:
        parse(json.loads(raw) if raw is not None else doc)
    if case["player"] == "schema-unsupported":
        assert isinstance(info.value, TourSchemaUnsupported)


def test_every_case_names_a_known_outcome():
    for case in CASES:
        assert case["player"] in ("ok", "invalid-data", "schema-unsupported", "load-failed"), case["name"]
        assert case["python"] in ("accept", "reject"), case["name"]
        assert (VALID / case["base"]).is_file(), case["name"]
