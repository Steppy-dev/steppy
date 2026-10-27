"""How a scenario becomes an in-app tour (``steppy.tours.inference``).

Each test writes one scenario, compiles it, and checks the kept tour steps,
the replay dispositions, and the diagnostics. The rules are the spec's §4.3
table plus the refinements recorded in docs/design/in-app-tours.md.
"""

import textwrap
from pathlib import Path

import pytest

from steppy.models import SteppyConfig
from steppy.parser import parse_scenario_file
from steppy.tours.compiler import compile_tours
from steppy.tours.settings import ToursSettings

LAYOUTS = {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}


def _settings(tmp_path, **tours):
    config = SteppyConfig(
        apps={
            "editor": {"name": "E", "url": "http://localhost:3000", "auth": {"mode": "bypass"}},
            "admin": {"name": "A", "url": "http://localhost:3000/admin/", "auth": {"mode": "bypass"}},
        },
        locales=["en", "fr"],
        default_locale="en",
        screenshot={"layouts": LAYOUTS},
        tours={"enabled": True, "targets": [{"type": "guide"}], **tours},
    )
    return ToursSettings.from_config(config, tmp_path)


def _compile(tmp_path, body: str, frontmatter: str = "tour: true", **tours):
    path = tmp_path / "scenarios" / "editor--case.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"---\ntitle: Case\n{frontmatter}\n---\n\n" + textwrap.dedent(body), encoding="utf-8")
    scenario = parse_scenario_file(path)
    result = compile_tours([scenario], _settings(tmp_path, **tours))
    return result


def _codes(result, severity=None):
    return [d.code for d in result.diagnostics if severity is None or d.severity == severity]


def _steps(result):
    (tour,) = result.tours
    return [(s.step_id, s.target, s.advance, s.wait_for) for s in tour.steps]


OPEN = """
### 1. Open
- role: editor
- action: navigate
- target: /invoices
"""


# ── the action table ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "action, extra, advance",
    [
        ("click", "", "click"),
        ("expand", "", "click"),
        ("toggle", "", "change"),
        ("select", "- value: EUR\n", "change"),
        ("select-option", "- option: first\n", "change"),
        ("pick-date", "- date: today\n", "change"),
        ("pick-time", '- time: "09:30"\n', "change"),
        ("fill", "- value: x\n", "input"),
        ("fill-date", "- date: today\n", "input"),
        ("upload", "- value: a.pdf\n", "change"),
    ],
)
def test_interactive_actions_point_at_their_test_id(tmp_path, action, extra, advance):
    result = _compile(
        tmp_path,
        OPEN + f"\n### 2. Act\n- role: editor\n- action: {action}\n- selector: '[data-testid=\"thing\"]'\n" + extra,
    )
    assert not _codes(result, "error"), [d.render() for d in result.diagnostics]
    assert _steps(result) == [("thing", "thing", advance, None)]


def test_fill_form_becomes_one_input_step_per_field(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Fill
- role: editor
- action: fill-form
- fields:
    '[data-testid="a"]': "one"
    '[data-testid="b"]': "two"
""",
    )
    assert _steps(result) == [("a", "a", "input", None), ("b", "b", "input", None)]
    tour = result.tours[0]
    assert [s.field for s in tour.steps] == ['[data-testid="a"]', '[data-testid="b"]']


def test_upload_behind_a_trigger_points_at_the_trigger_and_advances_on_next(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Upload
- role: editor
- action: upload
- selector: '[data-testid="file-input"]'
- value: a.pdf
- guide-cursor:
    behavior: click
    target: '[data-testid="upload-btn"]'
""",
    )
    assert _steps(result) == [("upload-btn", "upload-btn", "manual", None)]


def test_a_bare_file_input_upload_warns(tmp_path):
    result = _compile(
        tmp_path,
        OPEN + "\n### 2. Upload\n- role: editor\n- action: upload\n- selector: '[data-testid=\"f\"]'\n- value: a.pdf\n",
    )
    assert "upload-target-may-be-hidden" in _codes(result, "warning")


def test_skipped_actions_are_not_tour_steps(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Hover
- role: editor
- action: hover
- selector: '[data-testid="menu"]'

### 3. Wait
- role: editor
- action: wait
- duration: 1s

### 4. Key
- role: editor
- action: keyboard
- value: Escape

### 5. Click
- role: editor
- action: click
- selector: '[data-testid="go"]'
""",
    )
    assert _steps(result) == [("go", "go", "click", None)]
    assert "escape-in-tour" in _codes(result, "warning")


def test_confirm_and_interactive_custom_code_warn_about_unguided_actions(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Confirm
- role: editor
- action: confirm

### 3. Custom
- role: editor
- action: custom
- code: |
    page.locator('[data-testid="x"]').click()

### 4. Done
- role: editor
- action: click
- selector: '[data-testid="go"]'
""",
    )
    assert _codes(result, "warning").count("unguided-action") == 2


def test_custom_with_a_guide_cursor_is_a_step(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Custom
- role: editor
- action: custom
- guide-cursor:
    behavior: type
    target: '[data-testid="search"]'
- code: |
    page.fill('[data-testid="search"]', 'x')
""",
    )
    assert _steps(result) == [("search", "search", "input", None)]


def test_custom_with_text_but_no_cursor_is_a_centred_note(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + '\n### 2. Note\n- role: editor\n- action: custom\n- code: |\n    pass\n- guide-text:\n    en: "Look around."\n',
    )
    assert _steps(result) == [("note", None, "manual", None)]
    assert "unguided-action" in _codes(result, "warning")


# ── gates and info steps ──────────────────────────────────────────────────────


def test_a_wait_for_gates_the_next_step(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Wait for the list
- role: editor
- action: wait-for
- selector: '[data-testid="list"]'

### 3. New
- role: editor
- action: click
- selector: '[data-testid="new"]'
""",
    )
    assert _steps(result) == [("new", "new", "click", "list")]


def test_a_trailing_expect_gates_the_next_step_and_the_last_gate_wins(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Save
- role: editor
- action: click
- selector: '[data-testid="save"]'
- expect: visible([data-testid="saved"])

### 3. Wait
- role: editor
- action: wait-for
- selector: '[data-testid="detail"]'

### 4. Send
- role: editor
- action: click
- selector: '[data-testid="send"]'
""",
    )
    assert _steps(result)[1] == ("send", "send", "click", "detail")


def test_negative_assertions_and_hidden_waits_are_not_gates(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Gone
- role: editor
- action: assert
- expect: hidden([data-testid="spinner"])

### 3. Closed
- role: editor
- action: wait-for
- selector: '[data-testid="dialog"]'
- state: hidden

### 4. Go
- role: editor
- action: click
- selector: '[data-testid="go"]'
""",
    )
    assert _steps(result) == [("go", "go", "click", None)]


def test_an_assertion_with_text_is_an_anchored_info_step(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Save
- role: editor
- action: click
- selector: '[data-testid="save"]'

### 3. Created
- role: editor
- action: assert
- expect: visible([data-testid="detail"])
- guide-text:
    en: "Your invoice is saved."
""",
    )
    assert _steps(result)[-1] == ("detail", "detail", "manual", None)


def test_an_assertion_with_text_on_a_non_test_id_is_centred(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + '\n### 2. Heading\n- role: editor\n- action: assert\n- expect: text(h1, "Hi")\n- guide-text:\n    en: "Done."\n',
    )
    assert _steps(result) == [("heading", None, "manual", None)]


GATE = "\n### 2. Wait for the list\n- role: editor\n- action: wait-for\n- selector: '[data-testid=\"list\"]'\n"
BETWEEN = "\n### 3. Between\n- role: editor\n"
GO = "\n### 4. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n"


def test_a_gate_does_not_outlive_a_skipped_step_that_opens_another_page(tmp_path):
    # The first real consumer's ending: the gate is the list's table, a custom
    # step clicks a row and opens the detail page, and the closing note waited
    # for the table there — "Can't find this element", and a failed replay.
    result = _compile(
        tmp_path,
        OPEN
        + GATE
        + """
### 3. The new invoice is listed
- role: editor
- action: custom
- code: |
    expect(page.locator('[data-testid="list"]').get_by_text('Acme').first).to_be_visible(timeout=10000)

### 4. Open it
- role: editor
- action: custom
- code: |
    row = page.locator('[data-testid="list"]').get_by_text('Acme').first
    row.scroll_into_view_if_needed()
    row.click()

### 5. On its page
- role: editor
- action: assert
- expect: url-contains(/invoices/)

### 6. Wait for the heading
- role: editor
- action: wait-for
- selector: h1

### 7. Done
- role: editor
- action: assert
- expect: text(h1, "Acme")
- guide-text:
    en: "The invoice opens on its own page."
""",
    )
    assert not _codes(result, "error"), [d.render() for d in result.diagnostics]
    assert _steps(result) == [("done", None, "manual", None)]
    (plan,) = result.replay_plans.values()
    assert [ts.wait_for for entry in plan.entries for ts in entry.tour_steps] == [None]
    # Blamed on the step that clicks, not on the check before it.
    (stale,) = [d for d in result.diagnostics if d.code == "gate-stale"]
    assert (stale.severity, stale.step_number) == ("info", 4)
    assert "`click()`" in stale.message and "'list'" in stale.message and "step 7" in stale.message


@pytest.mark.parametrize(
    "body",
    [
        "- action: hover\n- selector: '[data-testid=\"menu\"]'\n",
        "- action: scroll\n- selector: '[data-testid=\"footer\"]'\n",
        '- action: wait\n- duration: "1s"\n',
        "- action: wait-for-text\n- value: Loaded\n",
        "- action: assert\n- expect: url-contains(/invoices)\n",
        "- action: wait-for\n- selector: '.spinner'\n- state: hidden\n",
        "- action: custom\n- code: |\n    page.wait_for_timeout(500)  # settle, then click() nothing\n"
        "    assert page.locator('[data-testid=\"list\"] tr').count() == 3, 'click(here)'\n",
        "- action: call\n- fn: helpers.sentinel_email\n- page: false\n",
    ],
    ids=["hover", "scroll", "wait", "wait-for-text", "assert", "wait-hidden", "custom-read", "call-no-page"],
)
def test_a_gate_holds_across_skipped_steps_that_leave_the_page_alone(tmp_path, body):
    result = _compile(tmp_path, OPEN + GATE + BETWEEN + body + GO)
    assert _steps(result) == [("go", "go", "click", "list")]
    assert "gate-stale" not in _codes(result)


def test_another_roles_step_does_not_drop_the_gate(tmp_path):
    # It acts in that role's own page and leaves this one as it was.
    other = "\n### 3. Elsewhere\n- role: admin\n- action: click\n- selector: '[data-testid=\"approve\"]'\n"
    result = _compile(tmp_path, OPEN + GATE + other + GO, frontmatter="tour: {role: editor}")
    assert _steps(result) == [("go", "go", "click", "list")]
    assert "gate-stale" not in _codes(result)


@pytest.mark.parametrize(
    "body, cause",
    [
        ("- action: click\n- selector: '[data-testid=\"row\"]'\n- tour: skip\n", "`click`"),
        ("- action: click\n- selector: '[data-testid=\"banner-close\"]'\n- optional: true\n", "`click`"),
        ("- action: fill\n- selector: '[data-testid=\"search\"]'\n- value: x\n- tour: skip\n", "`fill`"),
        ("- action: confirm\n", "`confirm`"),
        ("- action: keyboard\n- value: Enter\n", "`keyboard`"),
        ("- action: navigate\n- target: /invoices/1001\n", "`navigate`"),
        ("- action: custom\n- code: |\n    page.goto('/invoices/1001')\n", "`goto()`"),
        (
            "- action: custom\n- code: |\n    page.evaluate('''() => document.querySelector('form').reset()''')\n",
            "`evaluate()`",
        ),
        (
            "- action: custom\n- code: |\n    import sys; sys.path.insert(0, '.')\n    open_first_row(page)\n",
            "`open_first_row()`",
        ),
        (
            "- action: custom\n- code: |\n    with page.expect_download() as info:\n"
            "        page.locator('[data-testid=\"pdf\"]').click()\n",
            "`click()`",
        ),
        ("- action: call\n- fn: helpers.open_first_row\n", "`helpers.open_first_row`"),
    ],
    ids=["skip", "optional", "fill", "confirm", "key", "navigate", "goto", "evaluate", "helper", "waiter", "call"],
)
def test_a_skipped_step_that_may_change_the_page_drops_the_gate(tmp_path, body, cause):
    result = _compile(tmp_path, OPEN + GATE + BETWEEN + body + GO)
    assert _steps(result) == [("go", "go", "click", None)]
    (stale,) = [d for d in result.diagnostics if d.code == "gate-stale"]
    assert (stale.severity, stale.step_number) == ("info", 3)
    assert cause in stale.message and "'list'" in stale.message


@pytest.mark.parametrize(
    "between",
    [
        BETWEEN + "- action: click\n- selector: '[data-testid=\"row\"]'\n- tour: skip\n"
        "\n### 3b. Wait for the detail\n- role: editor\n- action: wait-for\n- selector: '[data-testid=\"detail\"]'\n",
        BETWEEN + "- action: click\n- selector: '[data-testid=\"row\"]'\n- tour: skip\n"
        '- expect: visible([data-testid="detail"])\n',
    ],
    ids=["check-after", "trailing-expect"],
)
def test_a_check_after_the_page_change_gates_the_next_step_again(tmp_path, between):
    # The fix the diagnostic suggests, and the reason it is only reported when a
    # tour step actually lost its wait.
    result = _compile(tmp_path, OPEN + GATE + between + GO)
    assert _steps(result) == [("go", "go", "click", "detail")]
    assert "gate-stale" not in _codes(result)


def test_a_dropped_gate_with_no_tour_step_after_it_is_not_reported(tmp_path):
    body = OPEN + GO.replace("### 4.", "### 1b.") + GATE + BETWEEN + "- action: keyboard\n- value: Enter\n"
    result = _compile(tmp_path, body)
    assert _steps(result) == [("go", "go", "click", None)]
    assert "gate-stale" not in _codes(result)


# ── targets ───────────────────────────────────────────────────────────────────


def test_a_kept_step_without_a_test_id_is_an_error_with_a_fix(tmp_path):
    result = _compile(
        tmp_path,
        OPEN + "\n### 2. Reject\n- role: editor\n- action: click\n- selector: button.p-confirmdialog-reject-button\n",
    )
    errors = [d for d in result.diagnostics if d.severity == "error"]
    assert [d.code for d in errors] == ["target-not-test-id"]
    assert "tour: skip" in errors[0].render()
    assert not result.tours


def test_host_plus_bare_tag_reduces_to_the_host(tmp_path):
    result = _compile(
        tmp_path,
        OPEN + "\n### 2. Pick\n- role: editor\n- action: select-option\n"
        "- selector: '[data-testid=\"customer\"] input'\n- option: first\n",
    )
    assert _steps(result) == [("customer", "customer", "change", None)]
    assert result.tours[0].steps[0].source_selector == '[data-testid="customer"] input'


def test_tour_skip_and_keep(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Reject
- role: editor
- action: click
- selector: button.reject
- tour: skip

### 3. Hover menu
- role: editor
- action: hover
- selector: '[data-testid="menu"]'
- tour: keep
- tour-text:
    en: "Hover the menu."

### 4. Note
- role: editor
- action: wait
- duration: 1s
- tour: keep
""",
    )
    assert not _codes(result, "error"), [d.render() for d in result.diagnostics]
    assert _steps(result) == [("menu", "menu", "manual", None), ("note", None, "manual", None)]


@pytest.mark.parametrize(
    "annotation, code",
    [
        ("- tour-advance: manual\n", "annotation-on-skipped-step"),
        ("- tour: skip\n- tour-placement: top\n", "skip-and-annotations"),
    ],
)
def test_annotations_on_skipped_steps_are_errors(tmp_path, annotation, code):
    result = _compile(
        tmp_path,
        OPEN
        + "\n### 2. Wait\n- role: editor\n- action: wait\n- duration: 1s\n"
        + annotation
        + "\n### 3. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
    )
    assert code in _codes(result, "error")


def test_keep_on_a_conditional_step_is_an_error(tmp_path):
    result = _compile(
        tmp_path,
        OPEN + "\n### 2. Maybe\n- role: editor\n- action: click\n- selector: '[data-testid=\"x\"]'\n"
        "- optional: true\n- tour: keep\n",
    )
    assert "keep-not-allowed" in _codes(result, "error")


@pytest.mark.parametrize("action, extra", [("expand", ""), ("toggle", "- state: off\n")])
def test_may_not_change_warns_until_the_advance_is_manual(tmp_path, action, extra):
    body = OPEN + f"\n### 2. Act\n- role: editor\n- action: {action}\n- selector: '[data-testid=\"x\"]'\n" + extra
    assert "may-not-change" in _codes(_compile(tmp_path, body), "warning")
    manual = _compile(tmp_path, body + "- tour-advance: manual\n")
    assert "may-not-change" not in _codes(manual)
    assert _steps(manual) == [("x", "x", "manual", None)]


def test_diagnostics_name_a_step_by_its_heading_as_well_as_its_position(tmp_path):
    act = "- role: editor\n- action: expand\n- selector: '[data-testid=\"x\"]'\n"
    body = OPEN + "\n### 2. Same\n" + act + "\n### 16. Renumbered\n" + act + "\n### 16b. Variant\n" + act
    lines = [d.render() for d in _compile(tmp_path, body).diagnostics if d.code == "may-not-change"]
    assert any(", step 2 'Same':" in line for line in lines), lines
    assert any(", step 3 (### 16) 'Renumbered':" in line for line in lines), lines
    assert any(", step 4 (### 16b) 'Variant':" in line for line in lines), lines


def test_an_advance_the_action_cannot_perform_is_an_error_with_replay(tmp_path):
    body = (
        OPEN
        + "\n### 2. Click\n- role: editor\n- action: click\n- selector: '[data-testid=\"x\"]'\n- tour-advance: input\n"
    )
    assert "advance-not-replayable" in _codes(_compile(tmp_path, body), "error")
    assert "advance-not-replayable" in _codes(_compile(tmp_path, body, replay=False), "warning")


# ── entry and start point ─────────────────────────────────────────────────────


def test_the_tour_starts_after_the_first_navigation(tmp_path):
    result = _compile(
        tmp_path, OPEN + "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n"
    )
    plan = result.replay_plans["editor--case"]
    assert plan.entry_route == "/invoices" and plan.start_index == 1
    assert [e.disposition for e in plan.entries] == ["setup", "tour"]


def test_login_steps_are_setup_and_the_entry_is_the_first_navigation_after_them(tmp_path):
    result = _compile(
        tmp_path,
        """
### 1. Home
- role: editor
- action: navigate
- target: /

### 2. Log in
- role: editor
- action: login

### 3. Open
- role: editor
- action: navigate
- target: /invoices

### 4. Go
- role: editor
- action: click
- selector: '[data-testid="go"]'
""",
    )
    plan = result.replay_plans["editor--case"]
    assert plan.entry_route == "/invoices"
    assert [e.disposition for e in plan.entries] == ["setup", "setup", "setup", "tour"]


def test_an_explicit_entry_matches_a_test_only_navigation_and_skips_its_guide_twin(tmp_path):
    result = _compile(
        tmp_path,
        """
### 1. Home
- role: editor
- action: navigate
- target: /

### 2. Members (test shortcut)
- role: editor
- action: navigate
- target: /members
- only-in: test

### 3. Open the menu
- role: editor
- action: click
- selector: '[data-testid="menu"]'
- only-in: guide

### 4. Pick members
- role: editor
- action: click
- selector: '[data-testid="menu-members"]'
- only-in: guide

### 5. Add
- role: editor
- action: click
- selector: '[data-testid="add"]'
""",
        frontmatter="tour:\n  entry: /members",
    )
    assert _steps(result) == [("add", "add", "click", None)]
    plan = result.replay_plans["editor--case"]
    assert [e.disposition for e in plan.entries] == ["setup", "setup", "drop", "drop", "tour"]


def test_an_entry_pattern_binds_params_from_the_navigation(tmp_path):
    result = _compile(
        tmp_path,
        "\n### 1. Open\n- role: editor\n- action: navigate\n- target: /invoices/1001\n"
        "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
        frontmatter="tour:\n  entry: /invoices/:invoiceId",
    )
    plan = result.replay_plans["editor--case"]
    assert plan.entry_route == "/invoices/:invoiceId" and plan.entry_params == (("invoiceId", "1001"),)


def test_an_interpolated_navigation_becomes_a_param(tmp_path):
    result = _compile(
        tmp_path,
        "\n### 1. Open\n- role: editor\n- action: navigate\n- target: /invoices/{{invoice_id}}\n"
        "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
    )
    plan = result.replay_plans["editor--case"]
    assert plan.entry_route == "/invoices/:invoice_id" and plan.entry_params == (("invoice_id", "{{invoice_id}}"),)


def test_fixture_ids_in_an_inferred_entry_warn(tmp_path):
    result = _compile(
        tmp_path,
        "\n### 1. Open\n- role: editor\n- action: navigate\n- target: /invoices/1001\n"
        "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
    )
    assert "entry-id-segment" in _codes(result, "warning")


def test_an_entry_that_matches_no_navigation_is_an_error(tmp_path):
    result = _compile(
        tmp_path,
        OPEN + "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
        frontmatter="tour:\n  entry: /customers",
    )
    assert "entry-no-match" in _codes(result, "error")


def test_no_navigation_at_all_is_an_error_with_replay_and_a_warning_without(tmp_path):
    body = "\n### 1. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n"
    assert "no-entry" in _codes(_compile(tmp_path, body), "error")
    assert "no-entry" in _codes(_compile(tmp_path, body, replay=False), "warning")


def test_later_navigation_warns(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + "\n### 2. Jump\n- role: editor\n- action: navigate\n- target: /invoices/77\n"
        + "\n### 3. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
    )
    nav = [d for d in result.diagnostics if d.code == "navigate-in-tour"]
    assert nav and "fixture ids" in nav[0].message


def test_routes_are_relative_to_the_app_base_path(tmp_path):
    path = tmp_path / "scenarios" / "admin--case.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        "---\ntitle: Admin\ntour: true\n---\n\n### 1. Open\n- role: admin\n- action: navigate\n- target: users\n"
        "\n### 2. Go\n- role: admin\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
        encoding="utf-8",
    )
    result = compile_tours([parse_scenario_file(path)], _settings(tmp_path))
    assert result.replay_plans["admin--case"].entry_route == "/users"


# ── the tour range: `tour: start` / `tour: end` ───────────────────────────────

# The guide walks from the invoice list to the customers by clicking; the tour
# is meant to start on the customer list.
WALK = """
### 1. Open the invoices
- role: editor
- action: navigate
- target: /invoices

### 2. Open Customers
- role: editor
- action: click
- selector: '[data-testid="nav-customers"]'

### 3. Wait for the list
- role: editor
- action: wait-for
- selector: '[data-testid="customer-list"]'
<3>
### 4. Remove the inactive ones
- role: editor
- action: click
- selector: '[data-testid="remove-inactive-btn"]'
<4>
### 5. Confirm
- role: editor
- action: confirm
- guide-text: {en: "Click Yes."}
<5>
### 6. Done
- role: editor
- action: assert
- expect: visible([data-testid="removed"])
<6>
"""


def _walk(**marks) -> str:
    body = WALK
    for n in range(3, 7):
        body = body.replace(f"<{n}>\n", marks.get(f"s{n}", "") + "\n")
    return body


def test_tour_start_starts_on_a_page_reached_by_clicking(tmp_path):
    result = _compile(tmp_path, _walk(s3="- tour: start"), frontmatter="tour:\n  entry: /customers")
    assert not _codes(result, "error"), [d.render() for d in result.diagnostics]
    assert "entry-unknown" not in _codes(result)
    # The marked wait-for is a gate: the first tour step waits for the list.
    assert _steps(result) == [("remove-inactive-btn", "remove-inactive-btn", "click", "customer-list")]
    plan = result.replay_plans["editor--case"]
    assert (plan.entry_route, plan.entry_params, plan.start_index) == ("/customers", (), 2)
    assert plan.start_in_place and plan.end_index is None
    assert [e.disposition for e in plan.entries] == ["setup", "setup", "run", "tour", "run", "run"]


def test_tour_start_without_an_entry_warns_when_clicks_led_there(tmp_path):
    result = _compile(tmp_path, _walk(s4="- tour: start"))
    assert not _codes(result, "error"), [d.render() for d in result.diagnostics]
    (diag,) = [d for d in result.diagnostics if d.code == "entry-unknown"]
    assert diag.severity == "warning" and diag.step_number == 4
    assert "step 2" in diag.message and "this `click` may change the page" in diag.message
    assert "tour.entry" in diag.hint
    plan = result.replay_plans["editor--case"]
    assert plan.entry_route is None and plan.start_index == 3


def test_tour_start_infers_the_entry_through_steps_that_leave_the_page_alone(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + "\n### 2. Menu (guide only)\n- role: editor\n- action: click\n- selector: '[data-testid=\"menu\"]'\n"
        + "- only-in: guide\n"
        + "\n### 3. Wait\n- role: editor\n- action: wait-for\n- selector: '[data-testid=\"invoice-list\"]'\n"
        + "\n### 4. Peek\n- role: editor\n- action: hover\n- selector: '[data-testid=\"help\"]'\n"
        + "\n### 5. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n- tour: start\n",
    )
    assert not _codes(result, "error") and "entry-unknown" not in _codes(result)
    plan = result.replay_plans["editor--case"]
    assert (plan.entry_route, plan.entry_params, plan.start_index) == ("/invoices", (), 4)
    assert [e.disposition for e in plan.entries] == ["setup", "drop", "setup", "setup", "tour"]
    # Steps before the start are setup, so their check no longer gates the tour.
    assert _steps(result) == [("go", "go", "click", None)]


def test_tour_start_with_no_navigation_before_it_has_no_entry(tmp_path):
    result = _compile(
        tmp_path,
        "\n### 1. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n- tour: start\n",
    )
    assert "no-entry" not in _codes(result) and "entry-unknown" in _codes(result, "warning")
    assert result.replay_plans["editor--case"].entry_route is None


def test_tour_end_runs_the_rest_after_the_tour(tmp_path):
    result = _compile(
        tmp_path,
        _walk(s4="- tour: start", s5="- tour: [end, keep]")
        + "\n### 7. Clean up\n- role: editor\n- action: click\n- selector: '[data-testid=\"undo\"]'\n"
        + "- only-in: guide\n",
        frontmatter="tour:\n  entry: /customers",
    )
    assert not _codes(result, "error"), [d.render() for d in result.diagnostics]
    assert [s[0] for s in _steps(result)] == ["remove-inactive-btn", "confirm"]
    plan = result.replay_plans["editor--case"]
    assert plan.end_index == 5
    assert [e.disposition for e in plan.entries] == ["setup", "setup", "setup", "tour", "tour", "after", "drop"]


def test_a_step_can_start_and_end_the_tour(tmp_path):
    result = _compile(tmp_path, _walk(s4="- tour: [start, end]"), frontmatter="tour:\n  entry: /customers")
    assert not _codes(result, "error"), [d.render() for d in result.diagnostics]
    assert [s[0] for s in _steps(result)] == ["remove-inactive-btn"]
    assert [e.disposition for e in result.replay_plans["editor--case"].entries][3:] == ["tour", "after", "after"]


def test_tour_end_alone_keeps_the_inferred_start(tmp_path):
    result = _compile(tmp_path, _walk(s4="- tour: end"))
    assert not _codes(result, "error"), [d.render() for d in result.diagnostics]
    assert [s[0] for s in _steps(result)] == ["nav-customers", "remove-inactive-btn"]
    plan = result.replay_plans["editor--case"]
    assert plan.start_index == 1 and not plan.start_in_place and plan.entry_route == "/invoices"


def test_annotations_after_the_end_are_errors(tmp_path):
    result = _compile(tmp_path, _walk(s4="- tour: end", s6='- tour-text: {en: "Gone."}'))
    (diag,) = [d for d in result.diagnostics if d.code == "annotation-outside-tour"]
    assert diag.step_number == 6 and "after the tour ends" in diag.message


@pytest.mark.parametrize(
    "marks, code, step",
    [
        ({"s3": "- tour: start", "s4": "- tour: start"}, "range-duplicate", 4),
        ({"s4": "- tour: end", "s6": "- tour: end"}, "range-duplicate", 6),
        ({"s4": "- tour: start", "s3": "- tour: end"}, "range-reversed", 3),
    ],
)
def test_a_tour_range_that_cannot_be_is_an_error(tmp_path, marks, code, step):
    result = _compile(tmp_path, _walk(**marks), frontmatter="tour:\n  entry: /customers")
    (diag,) = [d for d in result.diagnostics if d.code == code]
    assert diag.severity == "error" and diag.step_number == step
    assert not result.tours


def test_an_end_before_the_inferred_start_is_reversed(tmp_path):
    body = _walk().replace("- target: /invoices\n", "- target: /invoices\n- tour: end\n", 1)
    assert "range-reversed" in _codes(_compile(tmp_path, body), "error")


@pytest.mark.parametrize("action, extra", [("navigate", "- target: /customers\n"), ("login", "")])
def test_a_start_on_the_way_to_a_page_is_an_error(tmp_path, action, extra):
    result = _compile(
        tmp_path,
        OPEN
        + f"\n### 2. Move\n- role: editor\n- action: {action}\n{extra}- tour: start\n"
        + "\n### 3. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
    )
    (diag,) = [d for d in result.diagnostics if d.code == "start-on-navigation"]
    assert diag.step_number == 2 and "step after it" in diag.hint


def test_a_start_on_a_test_only_step_is_outside_the_tour(tmp_path):
    body = _walk().replace("- selector: '[data-testid=\"customer-list\"]'\n", "", 1)
    body = body.replace("- action: wait-for\n", "- action: wait\n- duration: 1s\n- only-in: test\n- tour: start\n", 1)
    assert "annotation-outside-tour" in _codes(_compile(tmp_path, body, frontmatter="tour:\n  entry: /customers"))


def test_without_markers_tour_entry_still_has_to_match_a_navigation(tmp_path):
    assert "entry-no-match" in _codes(_compile(tmp_path, _walk(), frontmatter="tour:\n  entry: /customers"), "error")


# ── roles, layouts, ids ───────────────────────────────────────────────────────


def test_multi_role_scenarios_need_a_tour_role(tmp_path):
    body = OPEN + "\n### 2. Admin\n- role: admin\n- action: click\n- selector: '[data-testid=\"x\"]'\n"
    assert "tour-role-required" in _codes(_compile(tmp_path, body), "error")
    result = _compile(
        tmp_path,
        body + "\n### 3. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
        frontmatter="tour:\n  role: editor",
    )
    assert _steps(result) == [("go", "go", "click", None)]
    assert [e.disposition for e in result.replay_plans["editor--case"].entries] == ["setup", "run", "tour"]


def test_layout_steps_need_media_queries(tmp_path):
    body = (
        OPEN
        + "\n### 2. Burger\n- role: editor\n- action: click\n- selector: '[data-testid=\"burger\"]'\n- layouts:\n  - mobile\n"
    )
    assert "layouts-unmapped" in _codes(_compile(tmp_path, body), "error")
    result = _compile(tmp_path, body, layouts={"desktop": "(min-width: 768px)", "mobile": "(max-width: 767.98px)"})
    assert result.tours[0].steps[0].layouts == ("mobile",)
    assert result.replay_plans["editor--case"].layouts == (("mobile", "(max-width: 767.98px)"),)


def test_step_ids_are_stable_when_a_step_is_inserted(tmp_path):
    a = _compile(tmp_path, OPEN + "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n")
    b = _compile(
        tmp_path,
        OPEN
        + "\n### 2. New\n- role: editor\n- action: click\n- selector: '[data-testid=\"new\"]'\n"
        + "\n### 3. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
    )
    assert a.tours[0].steps[0].step_id == b.tours[0].steps[1].step_id == "go"


def test_repeated_targets_get_suffixes(tmp_path):
    body = OPEN + "".join(
        f"\n### {n}. Next\n- role: editor\n- action: click\n- selector: '[data-testid=\"next\"]'\n" for n in (2, 3, 4)
    )
    assert [s[0] for s in _steps(_compile(tmp_path, body))] == ["next", "next-2", "next-3"]


def test_inference_does_not_depend_on_locales(tmp_path):
    body = OPEN + "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n"
    one = _compile(tmp_path, body, locales=["en"])
    three = _compile(tmp_path, body, locales=["en", "fr", "nl"])
    assert _steps(one) == _steps(three)


def test_zero_kept_steps_is_an_error(tmp_path):
    assert "no-kept-steps" in _codes(_compile(tmp_path, OPEN), "error")


def test_duplicate_tour_ids_across_scenarios(tmp_path):
    directory = tmp_path / "scenarios"
    directory.mkdir()
    for name in ("editor--a.md", "editor--b.md"):
        (directory / name).write_text(
            "---\ntitle: T\ntour: {id: Same}\n---\n" + OPEN + "\n### 2. Go\n- role: editor\n- action: click\n"
            "- selector: '[data-testid=\"go\"]'\n",
            encoding="utf-8",
        )
    scenarios = [parse_scenario_file(p) for p in sorted(directory.glob("*.md"))]
    result = compile_tours(scenarios, _settings(tmp_path))
    assert "tour-id-duplicate" in [d.code for d in result.diagnostics]


def test_scenarios_that_do_not_opt_in_are_ignored_unless_the_default_says_so(tmp_path):
    body = OPEN + "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n"
    assert not _compile(tmp_path, body, frontmatter="tags: [x]").tours
    assert _compile(tmp_path, body, frontmatter="tags: [x]", default=True).tours


def test_the_repo_scenarios_compile_when_forced_in(tmp_path):
    # Not a correctness oracle — a smoke test that inference never crashes on
    # the shapes the bundled example scenarios use.
    root = Path(__file__).resolve().parents[1]
    for path in sorted((root / "scenarios").glob("*.md")):
        scenario = parse_scenario_file(path)
        compile_tours([scenario], _settings(tmp_path, default=True))


def test_conditional_and_dropped_gate_steps_are_explained(tmp_path):
    # Both are infos (`steppy tours --explain`), each under its own code.
    result = _compile(
        tmp_path,
        OPEN
        + """
### 2. Maybe
- role: editor
- action: click
- selector: '[data-testid="banner-close"]'
- optional: true

### 3. Loaded
- role: editor
- action: wait-for
- selector: '.list > .row'

### 4. Go
- role: editor
- action: click
- selector: '[data-testid="go"]'
""",
    )
    infos = _codes(result, "info")
    assert "conditional-skipped" in infos and "gate-dropped" in infos
    assert _steps(result) == [("go", "go", "click", None)]


# ── tour-hold and hover menus ─────────────────────────────────────────────────

HOVER = """
### 2. Open the menu
- role: editor
- action: hover
- selector: '[data-testid="side-nav"]'
"""


def _click(n: int, test_id: str, extra: str = "") -> str:
    return f"\n### {n}. Click\n- role: editor\n- action: click\n- selector: '[data-testid=\"{test_id}\"]'\n" + extra


HOLD = "- tour-hold: '[data-testid=\"side-nav\"]'\n"


def test_a_hold_zone_reaches_the_tour_file_as_a_test_id(tmp_path):
    from steppy.tours.emit import tour_file_payload

    result = _compile(tmp_path, OPEN + HOVER + _click(3, "toggle", HOLD) + _click(4, "monthly", HOLD))
    assert not _codes(result, "error") and "hover-collapse-risk" not in _codes(result)
    (tour,) = result.tours
    assert [s.hold for s in tour.steps] == ["side-nav", "side-nav"]
    payload = tour_file_payload(tour, "en", "b-1")
    assert [s.get("hold") for s in payload["steps"]] == ["side-nav", "side-nav"]


def test_a_step_without_a_hold_emits_no_hold_key(tmp_path):
    from steppy.tours.emit import tour_file_payload

    result = _compile(tmp_path, OPEN + _click(2, "go"))
    assert "hold" not in tour_file_payload(result.tours[0], "en", "b-1")["steps"][0]


def test_a_hold_zone_that_is_not_a_test_id_is_an_error(tmp_path):
    result = _compile(tmp_path, OPEN + _click(2, "go", "- tour-hold: aside.sidebar\n"))
    errors = [d for d in result.diagnostics if d.severity == "error"]
    assert [d.code for d in errors] == ["target-not-test-id"]
    assert "tour-hold" in errors[0].render()


def test_a_hold_zone_on_a_centred_step_is_ignored_with_a_warning(tmp_path):
    result = _compile(
        tmp_path, OPEN + "\n### 2. Note\n- role: editor\n- action: wait\n- duration: 1s\n- tour: keep\n" + HOLD
    )
    assert "hold-ignored" in _codes(result, "warning")
    assert result.tours[0].steps[0].hold is None


def test_a_hold_zone_on_a_skipped_step_is_an_annotation_error(tmp_path):
    result = _compile(tmp_path, OPEN + HOVER + HOLD + _click(3, "go"))
    assert "annotation-on-skipped-step" in _codes(result, "error")


def test_a_click_after_a_hover_without_a_hold_warns_once(tmp_path):
    result = _compile(tmp_path, OPEN + HOVER + _click(3, "toggle") + _click(4, "monthly"))
    warnings = [d for d in result.diagnostics if d.code == "hover-collapse-risk"]
    assert len(warnings) == 1 and warnings[0].severity == "warning"
    assert "tour-hold" in warnings[0].render()


def test_a_held_chain_warns_on_the_first_click_that_drops_the_hold(tmp_path):
    result = _compile(tmp_path, OPEN + HOVER + _click(3, "toggle", HOLD) + _click(4, "monthly"))
    (warning,) = [d for d in result.diagnostics if d.code == "hover-collapse-risk"]
    assert "step 4" in warning.render()


@pytest.mark.parametrize(
    "between",
    [
        "",  # clicking the hovered element itself
        "\n### 3. Elsewhere\n- role: editor\n- action: navigate\n- target: /reports\n",
        "\n### 3. Type\n- role: editor\n- action: fill\n- selector: '[data-testid=\"q\"]'\n- value: x\n",
    ],
    ids=["same-element", "navigate", "other-kept-step"],
)
def test_no_hover_collapse_risk_when_the_hover_no_longer_matters(tmp_path, between):
    clicked = "side-nav" if not between else "monthly"
    result = _compile(tmp_path, OPEN + HOVER + between + _click(5, clicked))
    assert "hover-collapse-risk" not in _codes(result)


# ── tour-target-click ─────────────────────────────────────────────────────────


def _info(n: int, test_id: str, extra: str = "") -> str:
    return (
        f'\n### {n}. Look\n- role: editor\n- action: assert\n- expect: visible([data-testid="{test_id}"])\n'
        '- guide-text:\n    en: "This button creates one."\n' + extra
    )


def _payload_steps(result):
    from steppy.tours.emit import tour_file_payload

    return tour_file_payload(result.tours[0], "en", "b-1")["steps"]


def test_nothing_set_emits_no_target_click_key(tmp_path):
    result = _compile(tmp_path, OPEN + _info(2, "create"))
    assert [s.advance for s in result.tours[0].steps] == ["manual"]
    assert "targetClick" not in _payload_steps(result)[0]


@pytest.mark.parametrize("mode", ["pass", "next", "block"])
def test_a_step_target_click_reaches_an_info_step(tmp_path, mode):
    result = _compile(tmp_path, OPEN + _info(2, "create", f"- tour-target-click: {mode}\n"))
    assert not _codes(result, "error")
    assert _payload_steps(result)[0]["targetClick"] == mode


def test_the_config_default_applies_to_targeted_info_steps_only(tmp_path):
    result = _compile(tmp_path, OPEN + _info(2, "create") + _click(3, "go"), info_target_click="next")
    assert [s.get("targetClick") for s in _payload_steps(result)] == ["next", None]
    assert not {"target-click-on-action-step", "target-click-ignored"} & set(_codes(result))


def test_a_step_value_wins_over_the_config_default(tmp_path):
    result = _compile(tmp_path, OPEN + _info(2, "create", "- tour-target-click: pass\n"), info_target_click="next")
    assert _payload_steps(result)[0]["targetClick"] == "pass"


def test_a_target_click_on_an_action_step_is_an_error(tmp_path):
    result = _compile(tmp_path, OPEN + _click(2, "go", "- tour-target-click: next\n"))
    assert _codes(result, "error") == ["target-click-on-action-step"]


def test_tour_advance_manual_makes_a_click_step_an_info_step(tmp_path):
    result = _compile(
        tmp_path, OPEN + _click(2, "go", "- tour-advance: manual\n- tour-target-click: block\n"), replay=False
    )
    assert "target-click-on-action-step" not in _codes(result)
    assert _payload_steps(result)[0]["targetClick"] == "block"


def test_a_target_click_on_a_centred_step_is_ignored_with_a_warning(tmp_path):
    result = _compile(
        tmp_path,
        OPEN
        + "\n### 2. Note\n- role: editor\n- action: wait\n- duration: 1s\n- tour: keep\n- tour-target-click: next\n",
    )
    assert "target-click-ignored" in _codes(result, "warning")
    assert "targetClick" not in _payload_steps(result)[0]


def test_a_target_click_on_a_skipped_step_is_an_annotation_error(tmp_path):
    result = _compile(tmp_path, OPEN + HOVER + "- tour-target-click: next\n" + _click(3, "go"))
    assert "annotation-on-skipped-step" in _codes(result, "error")
