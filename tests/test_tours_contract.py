"""The producer-side in-app tour contract (``steppy.contracts.tours``).

The compiler runs every payload it writes through these models; the overlay
re-validates in the browser. These tests pin what the producer refuses, the
canonical byte form, and the cross-file consistency rules.
"""

import copy
import json

import pytest

from steppy.contracts.tours import (
    TourDataInvalid,
    TourSchemaUnsupported,
    check_consistency,
    dumps_canonical,
    parse_tour_file,
    parse_tours_manifest,
    sri_sha384,
)


def _tour(**over):
    base = {
        "schema": 1,
        "kind": "steppy-tour",
        "id": "create-invoice",
        "buildId": "demo-0001",
        "locale": "en",
        "title": "Create an invoice",
        "entry": {"route": "/invoices"},
        "steps": [
            {"id": "new-invoice-btn", "target": "new-invoice-btn", "advance": {"on": "click"}, "placement": "bottom"},
            {"id": "invoice-title", "target": "invoice-title", "advance": {"on": "input"}, "text": "Name it."},
            {"id": "done", "target": None, "advance": {"on": "manual"}, "title": "Done", "text": "All set."},
        ],
    }
    base.update(over)
    return base


def _manifest(files):
    return {
        "schema": 1,
        "kind": "steppy-tours-manifest",
        "buildId": "demo-0001",
        "defaultLocale": "en",
        "locales": ["en", "fr"],
        "testIdAttribute": "data-testid",
        "tours": [
            {
                "id": "create-invoice",
                "role": "editor",
                "title": {"en": "Create an invoice"},
                "entry": {"route": "/invoices"},
                "steps": 3,
                "files": {"en": "create-invoice.en.json"},
                "integrity": {"en": sri_sha384(files["create-invoice.en.json"])},
            }
        ],
    }


def test_a_valid_tour_and_manifest_pass_and_are_consistent():
    data = dumps_canonical(_tour())
    files = {"create-invoice.en.json": data}
    manifest = parse_tours_manifest(_manifest(files))
    parse_tour_file(json.loads(data))
    assert check_consistency(manifest, files) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.update(extra=1),
        lambda t: t.update(id=None),
        lambda t: t.update(id="Con"),
        lambda t: t.update(buildId="../x"),
        lambda t: t.update(buildId="con.v2"),
        lambda t: t.update(entry={"route": "/invoices/%2e%2e"}),
        lambda t: t.update(kind="something-else"),
        lambda t: t.update(entry={"route": "//evil.example"}),
        lambda t: t.update(entry={"route": "javascript:alert(1)"}),
        lambda t: t.update(guideUrl="javascript:alert(1)"),
        lambda t: t.update(guideUrl="https://user:pw@guide.example.com/x"),
        lambda t: t["steps"][0].update(target='[data-testid="new-invoice-btn"]'),
        lambda t: t["steps"][0].update(advance={"on": "hover"}),
        lambda t: t["steps"][0].update(placement="middle"),
        lambda t: t["steps"][0].update(target=None),
        lambda t: t["steps"][1].update(id="new-invoice-btn"),
        lambda t: t["steps"][1].update(text=" "),
        lambda t: t["steps"][1].update(text="x" * 2001),
        lambda t: t["steps"][1].update(html="<b>x</b>"),
        lambda t: t.update(steps=[]),
        lambda t: t.update(steps=[copy.deepcopy(t["steps"][2]) | {"id": f"s{i}"} for i in range(101)]),
        lambda t: t.update(schema="1"),
        lambda t: t.update(schema=True),
    ],
)
def test_the_producer_check_rejects_contract_violations(mutate):
    tour = _tour()
    mutate(tour)
    with pytest.raises(TourDataInvalid):
        parse_tour_file(tour)


def test_an_unknown_major_is_unsupported_not_invalid():
    with pytest.raises(TourSchemaUnsupported):
        parse_tour_file(_tour(schema=2))


def test_canonical_bytes_are_sorted_lf_and_keep_non_ascii():
    data = dumps_canonical({"b": "Créer", "a": 1})
    assert data == b'{\n  "a": 1,\n  "b": "Cr\xc3\xa9er"\n}\n'
    assert b"\r" not in data


def test_sri_matches_a_known_vector():
    # echo -n "" | openssl dgst -sha384 -binary | openssl base64 -A
    assert sri_sha384(b"") == "sha384-OLBgp1GsljhM2TJ+sbHjaiH9txEUvgdDTAzHv2P24donTt6/529l+9Ua0vFImLlb"


def test_manifest_rules():
    data = dumps_canonical(_tour())
    files = {"create-invoice.en.json": data}
    good = _manifest(files)
    parse_tours_manifest(good)

    bad = copy.deepcopy(good)
    bad["defaultLocale"] = "nl"
    with pytest.raises(TourDataInvalid):
        parse_tours_manifest(bad)

    bad = copy.deepcopy(good)
    bad["tours"][0]["files"] = {"en": "../escape.json"}
    with pytest.raises(TourDataInvalid):
        parse_tours_manifest(bad)

    bad = copy.deepcopy(good)
    zz = copy.deepcopy(bad["tours"][0]) | {"id": "zz", "files": {"en": "zz.en.json"}}
    bad["tours"].insert(0, zz)
    with pytest.raises(TourDataInvalid, match="sorted"):
        parse_tours_manifest(bad)

    bad = copy.deepcopy(good)
    bad["guideOrigin"] = "https://guide.example.com/path"
    with pytest.raises(TourDataInvalid):
        parse_tours_manifest(bad)

    bad = copy.deepcopy(good)
    bad["testIdAttribute"] = 'data-x"]'
    with pytest.raises(TourDataInvalid):
        parse_tours_manifest(bad)


def test_consistency_catches_cross_file_drift():
    data = dumps_canonical(_tour())
    files = {"create-invoice.en.json": data}
    manifest = parse_tours_manifest(_manifest(files))

    tampered = {"create-invoice.en.json": data.replace(b"Name it.", b"Name it!")}
    assert any("integrity" in p for p in check_consistency(manifest, tampered))

    other_build = dumps_canonical(_tour(buildId="other-1"))
    m2 = parse_tours_manifest(_manifest({"create-invoice.en.json": other_build}))
    assert any("buildId" in p for p in check_consistency(m2, {"create-invoice.en.json": other_build}))

    assert any("missing" in p for p in check_consistency(manifest, {}))

    with_link = dumps_canonical(_tour(guideUrl="https://guide.example.com/create-invoice/en.html"))
    m3 = parse_tours_manifest(_manifest({"create-invoice.en.json": with_link}))
    assert any("guideOrigin" in p for p in check_consistency(m3, {"create-invoice.en.json": with_link}))
