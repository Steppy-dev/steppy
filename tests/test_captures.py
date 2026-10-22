"""The capture-path grammar: formatting, parsing, and their round trip.

``step_stem`` used to be write-only, which is exactly why nothing could read the
capture tree back. Now that both directions live in one module, the property
worth pinning is that they are inverses — a formatter and a parser in separate
files can drift, one that a round-trip test covers cannot.
"""

from __future__ import annotations


import pytest

from steppy.captures import (
    CaptureRef,
    classify_capture_path,
    parse_step_stem,
    sanitise_nodeid,
    step_stem,
)


@pytest.mark.parametrize("step", [1, 7, 42, 99, 100, 1234])
@pytest.mark.parametrize("frame", [None, "a", "b", "z"])
@pytest.mark.parametrize("ext", ["png", "json"])
def test_parse_is_the_inverse_of_step_stem(step, frame, ext):
    parsed = parse_step_stem(f"{step_stem(step, frame)}.{ext}")
    assert parsed is not None
    assert (parsed.step, parsed.frame, parsed.ext) == (step, frame, ext)


@pytest.mark.parametrize(
    "name",
    [
        "step-1.png",  # the writer always zero-pads to two digits
        "step-01.webp",
        "step-01-AB.png",  # frames are lowercase letters
        "step-01-1.png",
        "notastep.png",
        "step-.png",
        "",
    ],
)
def test_non_captures_do_not_parse(name):
    assert parse_step_stem(name) is None


def test_flat_capture_has_no_locale_or_layout():
    ref = classify_capture_path(("onboarding", "step-01.png"))
    assert ref == CaptureRef("onboarding", None, None, 1, None, "png")


def test_two_segments_are_locale_then_layout_unambiguously():
    """The writer appends CURRENT_LOCALE then CURRENT_LAYOUT and never any
    other pair, so depth 2 needs no configuration to disambiguate."""
    ref = classify_capture_path(("order", "fr", "mobile", "step-03-b.json"))
    assert ref == CaptureRef("order", "fr", "mobile", 3, "b", "json")
    # ...even when the project declares neither name.
    assert classify_capture_path(("order", "xx", "yy", "step-01.png")) == CaptureRef(
        "order", "xx", "yy", 1, None, "png"
    )


def test_one_segment_resolves_by_configuration():
    """``<slug>/fr/`` and ``<slug>/desktop/`` are structurally identical."""
    locales, layouts = {"fr", "nl"}, {"desktop", "mobile"}
    assert classify_capture_path(("o", "fr", "step-01.png"), locales=locales, layouts=layouts).locale == "fr"
    assert classify_capture_path(("o", "fr", "step-01.png"), locales=locales, layouts=layouts).layout is None
    assert classify_capture_path(("o", "mobile", "step-01.png"), locales=locales, layouts=layouts).layout == "mobile"
    assert classify_capture_path(("o", "mobile", "step-01.png"), locales=locales, layouts=layouts).locale is None


def test_unknown_single_segment_reads_as_a_locale():
    """Locale beats layout, mirroring the guide read side's precedence: showing
    the wrong language is worse than showing the wrong viewport."""
    ref = classify_capture_path(("o", "de", "step-01.png"), locales={"fr"}, layouts={"mobile"})
    assert (ref.locale, ref.layout) == ("de", None)


def test_failures_tree_is_not_a_capture():
    assert classify_capture_path(("_failures", "some__nodeid.png")) is None


def test_deeper_than_the_writer_can_produce_is_rejected():
    assert classify_capture_path(("o", "fr", "mobile", "extra", "step-01.png")) is None


def test_guide_reexports_the_same_objects():
    """``build.py`` imports ``step_stem`` from ``guide``; the definition moved
    but the import must keep resolving to the one function."""
    from steppy import captures, guide

    assert guide.step_stem is captures.step_stem
    assert guide._has_captures is captures._has_captures


def test_sanitise_nodeid_mirrors_the_conftest_template():
    """Byte-mirrored pair, like ``totp._totp_now``. The Python copy exists so
    the bundler can build a forward map from nodeid to filename."""
    from pathlib import Path

    template = (Path(__file__).parent.parent / "templates" / "conftest.py.j2").read_text(encoding="utf-8")
    body = template.split("def _sanitise_nodeid(nodeid: str) -> str:", 1)[1].split("def ", 1)[0]
    replacements = [line.strip() for line in body.splitlines() if line.strip().startswith(".replace(")]
    assert replacements, "could not locate _sanitise_nodeid's body in conftest.py.j2"

    namespace: dict = {}
    exec(
        "def _sanitise_nodeid(nodeid):\n    return (nodeid\n" + "\n".join(f"    {r}" for r in replacements) + "\n    )",
        namespace,
    )
    template_fn = namespace["_sanitise_nodeid"]

    for nodeid in (
        "tests/e2e/test_order.py::test_flow[fr-mobile]",
        "tests\\e2e\\test_a_b.py::test_x",
        "plain",
    ):
        assert sanitise_nodeid(nodeid) == template_fn(nodeid), (
            "steppy.captures.sanitise_nodeid has drifted from _sanitise_nodeid in "
            "templates/conftest.py.j2 — the two name the same files and must stay identical"
        )
