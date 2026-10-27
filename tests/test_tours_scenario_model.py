"""In-app tour annotations on scenarios: the frontmatter ``tour:`` block and the
``tour`` / ``tour-text`` / ``tour-advance`` / ``tour-placement`` step keys."""

import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from steppy import tour_spec
from steppy.models import _FIELD_ALIASES, _UNIVERSAL_FIELDS, VALID_ACTIONS, Scenario, Step
from steppy.parser import ParseError, _structured_step_keys, parse_scenario_file

# A minimal valid body per action, so every action can carry the tour keys.
_BODIES = {
    "navigate": {"target": "/invoices"},
    "click": {"selector": "x"},
    "fill": {"selector": "x", "value": "v"},
    "fill-form": {"fields": {"x": "v"}},
    "select": {"selector": "x", "value": "v"},
    "wait": {"duration": "1s"},
    "wait-for": {"selector": "x"},
    "wait-for-text": {"value": "v"},
    "scroll": {"selector": "x"},
    "hover": {"selector": "x"},
    "keyboard": {"value": "Enter"},
    "assert": {"expect": "visible(x)"},
    "custom": {"code": "pass"},
    "login": {},
    "capture": {"store": "v", "from": {"type": "date", "date": "today"}},
    "upload": {"selector": "x", "value": "f.pdf"},
    "select-option": {"selector": "x", "option": "first"},
    "confirm": {},
    "fill-date": {"selector": "x", "date": "today"},
    "toggle": {"selector": "x"},
    "pick-date": {"selector": "x", "date": "today"},
    "pick-time": {"selector": "x", "time": "09:30"},
    "expand": {"selector": "x"},
    "call": {"fn": "helpers.ping"},
}


def test_every_action_has_a_body_here():
    assert set(_BODIES) == VALID_ACTIONS


@pytest.mark.parametrize("action", sorted(VALID_ACTIONS))
@pytest.mark.parametrize("mode", tour_spec.STEP_TOUR_MODES)
def test_every_action_accepts_the_tour_keys(action, mode):
    step = Step(
        action=action,
        role="editor",
        **_BODIES[action],
        **{"tour": mode, "tour-text": {"en": "Text"}, "tour-advance": "manual", "tour-placement": "top"},
    )
    assert step.tour == [mode] and step.tour_advance == "manual" and step.tour_placement == "top"


@pytest.mark.parametrize(
    "value, stored",
    [
        ("keep", ["keep"]),
        (" start ", ["start"]),
        (["end", "keep"], ["end", "keep"]),
        (["start", "end"], ["start", "end"]),
        (["start", "skip"], ["start", "skip"]),
    ],
)
def test_step_tour_takes_one_value_or_a_list(value, stored):
    assert Step(action="click", selector="x", role="e", tour=value).tour == stored


@pytest.mark.parametrize(
    "value, message",
    [
        (["skip", "keep"], "both 'skip' and 'keep'"),
        (["end", "end"], "repeats"),
        ([], "at least one"),
        (["start", "middle"], "start"),
    ],
)
def test_contradictory_step_tour_lists_are_rejected(value, message):
    with pytest.raises(ValidationError, match=message):
        Step(action="click", selector="x", role="e", tour=value)


def test_the_value_sets_are_the_contract_vocabulary():
    hints = typing.get_type_hints(Step)

    def literal_args(name):
        (inner,) = [a for a in typing.get_args(hints[name]) if a is not type(None)]
        if typing.get_origin(inner) is list:  # `tour` holds one value or a list, stored as a list
            (inner,) = typing.get_args(inner)
        return typing.get_args(inner)

    assert literal_args("tour") == tour_spec.STEP_TOUR_MODES
    assert literal_args("tour_advance") == tour_spec.ADVANCE_KINDS
    assert literal_args("tour_placement") == tour_spec.PLACEMENTS
    assert literal_args("tour_target_click") == tour_spec.TARGET_CLICK_MODES


def test_the_keys_are_registered_for_every_action():
    assert {"tour", "tour_text", "tour_advance", "tour_placement", "tour_hold"} <= _UNIVERSAL_FIELDS
    assert _FIELD_ALIASES["tour_text"] == "tour-text"
    assert _FIELD_ALIASES["tour_hold"] == "tour-hold"
    # A selector is a plain string: never parsed as a YAML flow sequence.
    assert "tour-hold" not in _structured_step_keys()
    assert {"tour-text", "tour"} <= _structured_step_keys()


@pytest.mark.parametrize("value", ["true", "false", "yes", "on", True, ["start", "true"]])
def test_a_boolean_step_tour_points_at_the_frontmatter(value):
    with pytest.raises(ValidationError, match="frontmatter"):
        Step(action="click", selector="x", role="e", tour=value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tour": "maybe"},
        {"tour-advance": "hover"},
        {"tour-placement": "middle"},
        {"tour-text": {}},
        {"tour-text": {"en": None}},
        {"tour-text": {"not a locale": "x"}},
    ],
)
def test_bad_annotation_values_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        Step(action="click", selector="x", role="e", **kwargs)


def test_tour_text_takes_every_guide_text_shape():
    nested = Step(action="click", selector="x", role="e", **{"tour-text": {"en": {"title": "T", "text": "Body"}}})
    legacy = Step(action="click", selector="x", role="e", **{"tour-text": {"fr": "Une phrase. Puis une autre."}})
    regional = Step(action="click", selector="x", role="e", **{"tour-text": {"fr-BE": "Wallon"}})
    assert nested.tour_text.get("en").text == "Body"
    assert legacy.tour_text.get("fr").title == "Une phrase"
    assert regional.tour_text.get("fr-BE").text == "Wallon"


# ── frontmatter ───────────────────────────────────────────────────────────────


def test_frontmatter_tour_forms():
    assert Scenario(title="t").tour is None
    assert Scenario(title="t", tour=True).tour.enabled is True
    assert Scenario(title="t", tour=False).tour.enabled is False
    block = Scenario(
        title="t",
        tour={
            "id": "create-invoice",
            "title": {"en": "Create an invoice", "fr": "Créer une facture"},
            "entry": "/invoices/:invoiceId",
            "role": "editor",
            "guide_slug": "create-invoice",
        },
    ).tour
    assert block.enabled and block.id == "create-invoice" and block.entry == "/invoices/:invoiceId"


def test_opt_in_follows_the_project_default_unless_the_scenario_says():
    assert Scenario(title="t").tour_opted_in(True) is True
    assert Scenario(title="t").tour_opted_in(False) is False
    assert Scenario(title="t", tour=False).tour_opted_in(True) is False
    assert Scenario(title="t", tour={"id": "x"}).tour_opted_in(False) is True


@pytest.mark.parametrize(
    "tour",
    [
        "yes please",
        {"id": "-bad"},
        {"id": "nul"},
        {"title": ""},
        {"title": {}},
        {"title": {"en": " "}},
        {"entry": "invoices"},
        {"entry": "//evil.example"},
        {"entry": "/x?y=1"},
        {"entry": "/x#y"},
        {"guide_slug": "a/b"},
        {"role": " "},
        {"bogus": 1},
    ],
)
def test_bad_frontmatter_blocks_are_rejected(tour):
    with pytest.raises(ValidationError):
        Scenario(title="t", tour=tour)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "editor--create-invoice.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_scenario_file_round_trips(tmp_path):
    path = _write(
        tmp_path,
        """---
title: Create an invoice
tour:
  id: create-invoice
  title: {en: Create an invoice, fr: Créer une facture}
---

### 1. Open the list
- role: editor
- action: navigate
- target: /invoices

### 2. Start a new invoice
- role: editor
- action: click
- selector: '[data-testid="new-invoice-btn"]'
- tour-placement: bottom
- tour-text:
    en:
      title: New invoice
      text: Click **New invoice**.
    fr: Cliquez sur **Nouvelle facture**.

### 3. A note
- role: editor
- action: wait
- duration: 1s
- tour: keep
- tour-advance: manual
- tour-text: {en: "Take a moment."}

### 4. Confirm
- role: editor
- action: confirm
- tour: [end, keep]

### 5. Check
- role: editor
- action: assert
- expect: visible([data-testid="done"])
- tour:
    - start
    - end
""",
    )
    scenario = parse_scenario_file(path)
    assert scenario.tour.id == "create-invoice"
    assert scenario.steps[1].tour_placement == "bottom"
    assert scenario.steps[1].tour_text.get("en").title == "New invoice"
    assert scenario.steps[2].tour == ["keep"] and scenario.steps[2].tour_text.get("en").text == "Take a moment."
    assert scenario.steps[3].tour == ["end", "keep"]
    assert scenario.steps[4].tour == ["start", "end"]


def test_an_unknown_tour_key_fails_parsing(tmp_path):
    path = _write(
        tmp_path,
        """---
title: t
---

### 1. Click
- role: editor
- action: click
- selector: x
- tour-txt: oops
""",
    )
    with pytest.raises(ParseError, match="tour-txt"):
        parse_scenario_file(path)
