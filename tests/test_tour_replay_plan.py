"""``steppy.tours.replay.build_replay``: which steps a replay runs, in which order, with which checks."""

import dataclasses
import textwrap
from pathlib import Path

import pytest

from steppy.config import load_config
from steppy.models import SteppyConfig
from steppy.parser import parse_scenario_file, parse_scenarios_from_directory
from steppy.tours.compiler import compile_tours
from steppy.tours.replay import ReplayPlanError, build_replay
from steppy.tours.settings import ToursSettings

DEMO = Path(__file__).resolve().parents[1] / "fixtures" / "tour-demo" / "project"


def _demo(slug):
    config = load_config(DEMO / "config.yaml")
    scenarios = {s.slug: s for s in parse_scenarios_from_directory(DEMO / "scenarios")}
    plans = compile_tours(list(scenarios.values()), ToursSettings.from_config(config, DEMO)).replay_plans
    return scenarios[slug], plans[slug], config


def _case(tmp_path, body, frontmatter="tour: true"):
    config = SteppyConfig(
        apps={
            "editor": {"name": "E", "url": "http://localhost:3000", "auth": {"mode": "bypass"}},
            "reviewer": {"name": "R", "url": "http://localhost:3000", "auth": {"mode": "bypass"}},
        },
        locales=["en"],
        default_locale="en",
        tours={"enabled": True, "targets": [{"type": "guide"}]},
    )
    path = tmp_path / "editor--case.md"
    path.write_text(f"---\ntitle: Case\n{frontmatter}\n---\n" + textwrap.dedent(body), encoding="utf-8")
    scenario = parse_scenario_file(path)
    result = compile_tours([scenario], ToursSettings.from_config(config, tmp_path))
    assert not result.has_errors, [d.render() for d in result.diagnostics]
    plan = result.replay_plans["editor--case"]
    return scenario, plan, config, build_replay(scenario, plan, config)


def _kinds(replay):
    return [item.kind for item in replay.items]


def _step(n, title, action, extra=""):
    return f"\n### {n}. {title}\n- role: editor\n- action: {action}\n{extra}"


OPEN = _step(1, "Open", "navigate", "- target: /invoices\n")


def test_the_demo_replay_sequence():
    scenario, plan, config = _demo("editor--create-invoice")
    replay = build_replay(scenario, plan, config)
    assert _kinds(replay) == ["setup", "start", "plain", "tour", "tour", "tour", "tour", "tour", "complete"]
    assert replay.function_name == "test_editor__create_invoice__tour"
    assert (replay.role, replay.roles, replay.auth) == ("editor", ["editor"], True)
    checks = [check for item in replay.items for _step, check in item.parts]
    assert [(c.step_id, c.advance, c.ensure) for c in checks] == [
        ("new-invoice-btn", "click", "inline"),
        ("invoice-title", "input", "inline"),
        ("customer-select", "change", "inline"),
        ("save-invoice-btn", "click", "inline"),
        ("invoice-detail", "manual", "inline"),
    ]
    assert checks[0].selector == '[data-testid="new-invoice-btn"]'


def test_the_spec_carries_entry_params_and_steps():
    scenario, plan, config = _demo("editor--send-invoice")
    replay = build_replay(scenario, plan, config)
    assert replay.params == [("invoiceId", "1001")]
    assert replay.spec["entry"] == {"route": "/invoices/:invoiceId", "params": [["invoiceId", "1001"]]}
    assert [s["id"] for s in replay.spec["steps"]] == ["send-to", "send-message", "send-invoice-btn", "invoice-status"]
    assert replay.spec["steps"][0]["field"] == '[data-testid="send-to"]'
    assert replay.spec["attribute"] == "data-testid" and replay.spec["timeout_ms"] > 0


def test_fill_form_splits_into_one_part_per_field_and_keeps_its_tail_on_the_last():
    scenario, plan, config = _demo("editor--send-invoice")
    (tour,) = [i for i in build_replay(scenario, plan, config).items if i.kind == "tour" and len(i.parts) > 1]
    (first, _c1), (last, _c2) = tour.parts
    assert list(first.fields) == ['[data-testid="send-to"]'] and list(last.fields) == ['[data-testid="send-message"]']
    original = scenario.steps[1]
    assert first.wait_after is None and first.expect is None
    assert (last.wait_after, last.expect) == (original.wait_after, original.expect)


def test_copies_capture_nothing_and_leave_the_scenario_untouched(tmp_path):
    body = OPEN + _step(2, "Go", "click", "- selector: '[data-testid=\"go\"]'\n- screenshot: true\n- only-in: guide\n")
    scenario, _plan, _config, replay = _case(tmp_path, body)
    (tour,) = [i for i in replay.items if i.kind == "tour"]
    copy, _check = tour.parts[0]
    assert copy.screenshot is None and copy.only_in is None
    assert scenario.steps[1].screenshot is not None and scenario.steps[1].only_in == "guide"


def test_setup_runs_in_test_mode_and_the_range_in_tour_mode(tmp_path):
    body = (
        _step(1, "Open", "navigate", "- target: /invoices?seed=1\n- only-in: test\n")
        + _step(2, "Open (guide)", "navigate", "- target: /invoices\n- only-in: guide\n")
        + _step(3, "Check", "assert", '- expect: visible([data-testid="list"])\n- only-in: test\n')
        + _step(4, "Go", "click", "- selector: '[data-testid=\"go\"]'\n")
    )
    _scenario, plan, _config, replay = _case(tmp_path, body)
    assert plan.start_index == 2  # the entry navigate plus its guide twin
    assert _kinds(replay) == ["setup", "omitted", "start", "omitted", "tour", "complete"]
    assert "guide-only" in replay.items[1].comment and "test-only" in replay.items[3].comment


def test_another_roles_steps_run_plainly_inside_the_range(tmp_path):
    body = (
        OPEN
        + _step(2, "Go", "click", "- selector: '[data-testid=\"go\"]'\n")
        + "\n### 3. Review\n- role: reviewer\n- action: navigate\n- target: /review\n"
        + _step(4, "Next", "click", "- selector: '[data-testid=\"next\"]'\n")
    )
    _scenario, _plan, _config, replay = _case(tmp_path, body, frontmatter="tour:\n  role: editor")
    assert _kinds(replay) == ["setup", "start", "tour", "plain", "tour", "complete"]
    assert replay.roles == ["editor", "reviewer"]


@pytest.mark.parametrize(
    "action, extra", [("keyboard", "- value: Enter\n"), ("custom", "- code: page.keyboard.press('Enter')\n")]
)
def test_next_is_deferred_past_a_focus_dependent_step(tmp_path, action, extra):
    body = (
        OPEN
        + _step(2, "Search", "fill", "- selector: '[data-testid=\"q\"]'\n- value: acme\n")
        + _step(3, "Submit", action, extra)
        + _step(4, "Open", "click", "- selector: '[data-testid=\"row\"]'\n")
    )
    _scenario, _plan, _config, replay = _case(tmp_path, body)
    assert _kinds(replay) == ["setup", "start", "tour", "plain", "deferred-ensure", "tour", "complete"]
    search = replay.items[2].parts[0][1]
    assert (search.step_id, search.ensure) == ("q", "deferred") and replay.items[4].step_id == "q"


def test_a_deferred_next_at_the_end_still_runs_before_completion(tmp_path):
    body = (
        OPEN
        + _step(2, "Search", "fill", "- selector: '[data-testid=\"q\"]'\n- value: acme\n")
        + _step(3, "Submit", "keyboard", "- value: Enter\n")
    )
    _scenario, _plan, _config, replay = _case(tmp_path, body)
    assert _kinds(replay) == ["setup", "start", "tour", "plain", "deferred-ensure", "complete"]


def test_a_click_step_is_never_deferred(tmp_path):
    body = (
        OPEN
        + _step(2, "Go", "click", "- selector: '[data-testid=\"go\"]'\n")
        + _step(3, "Submit", "keyboard", "- value: Enter\n")
    )
    _scenario, _plan, _config, replay = _case(tmp_path, body)
    assert "deferred-ensure" not in _kinds(replay)


# ── `tour: start` / `tour: end` ───────────────────────────────────────────────

RANGED = (
    OPEN
    + _step(2, "Customers", "click", "- selector: '[data-testid=\"nav-customers\"]'\n")
    + _step(3, "Search", "fill", "- selector: '[data-testid=\"q\"]'\n- value: acme\n- tour: start\n")
    + _step(4, "Submit", "keyboard", "- value: Enter\n- tour: end\n")
    + _step(5, "Clean up", "custom", "- code: page.keyboard.press('Escape')\n")
    + _step(6, "Guide note", "click", "- selector: '[data-testid=\"x\"]'\n- only-in: guide\n")
)


def test_steps_after_the_end_run_once_the_tour_completed(tmp_path):
    _scenario, plan, _config, replay = _case(tmp_path, RANGED, frontmatter="tour:\n  entry: /customers")
    assert plan.end_index == 4
    assert _kinds(replay) == [
        "setup",
        "setup",
        "start",
        "tour",
        "plain",
        "deferred-ensure",
        "complete",
        "after",
        "omitted",
    ]
    assert replay.items[7].comment == "Step 5: Clean up — after the tour"
    assert replay.items[2].comment == "Tour 'editor--case' starts here (in place, entry /customers)"


def test_a_focus_dependent_step_after_the_end_does_not_defer_next(tmp_path):
    body = RANGED.replace("- value: Enter\n- tour: end\n", "- value: Enter\n").replace(
        "- value: acme\n- tour: start\n", "- value: acme\n- tour: [start, end]\n"
    )
    _scenario, _plan, _config, replay = _case(tmp_path, body, frontmatter="tour:\n  entry: /customers")
    assert _kinds(replay) == ["setup", "setup", "start", "tour", "complete", "after", "after", "omitted"]
    assert replay.items[3].parts[0][1].ensure == "inline"


def test_an_in_place_start_is_flagged_in_the_spec_and_passes_no_params(tmp_path):
    _scenario, _plan, _config, replay = _case(tmp_path, RANGED, frontmatter="tour:\n  entry: /customers/:customerId")
    assert replay.spec["entry"] == {"route": "/customers/:customerId", "params": [], "in_place": True}
    assert replay.params == []


def test_the_spec_is_unchanged_without_a_start_marker(tmp_path):
    body = RANGED.replace("- tour: start\n", "")
    _scenario, _plan, _config, replay = _case(tmp_path, body)
    assert replay.spec["entry"] == {"route": "/invoices", "params": []}


# ── Plans that do not line up are compiler bugs ─────────────────────────────


def _broken(plan, **changes):
    return dataclasses.replace(plan, **changes)


def _retarget(entry, **changes):
    return dataclasses.replace(entry, **changes)


def test_misaligned_plans_are_rejected():
    scenario, plan, config = _demo("editor--create-invoice")
    entries = list(plan.entries)
    cases = {
        "missing entry": _broken(plan, entries=tuple(entries[:-1])),
        "wrong step number": _broken(plan, entries=(_retarget(entries[0], step_number=9), *entries[1:])),
        "tour step in setup": _broken(plan, start_index=4),
        "wrong count": _broken(plan, tour_step_count=plan.tour_step_count + 1),
        "out of order": _broken(
            plan,
            entries=(
                *entries[:2],
                _retarget(entries[2], tour_steps=(dataclasses.replace(entries[2].tour_steps[0], index=3),)),
                *entries[3:],
            ),
        ),
    }
    for name, broken in cases.items():
        with pytest.raises(ReplayPlanError):
            build_replay(scenario, broken, config)
        assert name  # labels the failing case in the traceback


def test_tour_steps_past_the_end_and_after_steps_inside_the_range_are_rejected(tmp_path):
    scenario, plan, config, _replay = _case(tmp_path, RANGED, frontmatter="tour:\n  entry: /customers")
    entries = list(plan.entries)
    cases = {
        "end before a tour step": _broken(plan, end_index=2),
        "after inside the range": _broken(
            plan, entries=(*entries[:3], _retarget(entries[3], disposition="after"), *entries[4:])
        ),
    }
    for name, broken in cases.items():
        with pytest.raises(ReplayPlanError):
            build_replay(scenario, broken, config)
        assert name


def test_an_unknown_fill_form_field_is_rejected():
    scenario, plan, config = _demo("editor--send-invoice")
    entries = list(plan.entries)
    bad = dataclasses.replace(entries[1].tour_steps[0], field='[data-testid="nope"]')
    broken = _broken(
        plan, entries=(entries[0], _retarget(entries[1], tour_steps=(bad, *entries[1].tour_steps[1:])), *entries[2:])
    )
    with pytest.raises(ReplayPlanError, match="is not a field"):
        build_replay(scenario, broken, config)
