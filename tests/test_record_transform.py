"""Tests for the `steppy record` codegen→scenario transform (pure, no browser).

Feeds known ``playwright codegen --target python-pytest`` source strings into
:func:`steppy.record_cli.codegen_source_to_scenario` and asserts the emitted
Markdown, plus a round-trip through the parser + generator. Also covers the
runtime ``_interp_val`` helper that resolves ${VAR} in recorded fill values.
"""

import ast
import re
from pathlib import Path

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.parser import parse_scenario_file
from steppy.record_cli import codegen_source_to_scenario


def _wrap(body: list[str]) -> str:
    lines = "".join(f"    {line}\n" for line in body)
    return (
        "import re\nfrom playwright.sync_api import Page, expect\n\n\ndef test_example(page: Page) -> None:\n" + lines
    )


def _tx(src: str, **kw):
    kw.setdefault("role", "admin")
    kw.setdefault("title", "Recorded flow")
    return codegen_source_to_scenario(src, **kw)


# ── Locator → selector mappings ──────────────────────────────────────────────


def test_click_test_id():
    r = _tx(_wrap(['page.get_by_test_id("menu").click()']))
    assert "- action: click" in r.markdown
    assert '- selector: [data-testid="menu"]' in r.markdown


def test_fill_literal_value():
    r = _tx(_wrap(['page.get_by_test_id("name").fill("Alice")']))
    assert "- action: fill" in r.markdown
    assert '- value: "Alice"' in r.markdown


def test_role_selector_with_name():
    r = _tx(_wrap(['page.get_by_role("button", name="Save").click()']))
    assert '- selector: role=button[name="Save"]' in r.markdown


def test_placeholder_selector():
    r = _tx(_wrap(['page.get_by_placeholder("Search").click()']))
    assert '- selector: [placeholder="Search"]' in r.markdown


def test_locator_raw_css():
    r = _tx(_wrap(['page.locator(".sidebar .item").click()']))
    assert "- selector: .sidebar .item" in r.markdown


def test_native_select_option():
    r = _tx(_wrap(['page.get_by_role("combobox", name="Country").select_option("BE")']))
    assert "- action: select" in r.markdown
    assert '- value: "BE"' in r.markdown


def test_keyboard_press():
    r = _tx(_wrap(['page.get_by_test_id("q").press("Enter")']))
    assert "- action: keyboard" in r.markdown
    assert '- value: "Enter"' in r.markdown


def test_hover_and_upload():
    r = _tx(
        _wrap(
            [
                'page.get_by_test_id("avatar").hover()',
                'page.get_by_test_id("file").set_input_files("/tmp/x.pdf")',
            ]
        )
    )
    assert "- action: hover" in r.markdown
    assert "- action: upload" in r.markdown
    assert '- value: "/tmp/x.pdf"' in r.markdown


def test_check_becomes_click_with_note():
    r = _tx(_wrap(['page.get_by_test_id("agree").check()']))
    assert "- action: click" in r.markdown
    assert any("check" in n for n in r.notes)


# ── Positional (.nth/.first/.last) fold into a >> nth= suffix ─────────────────


def test_first_folds_nth():
    r = _tx(_wrap(['page.get_by_test_id("row").first.click()']))
    assert '- selector: [data-testid="row"] >> nth=0' in r.markdown


def test_nth_index_folds():
    r = _tx(_wrap(['page.get_by_test_id("row").nth(2).click()']))
    assert '- selector: [data-testid="row"] >> nth=2' in r.markdown


def test_last_folds_nth():
    r = _tx(_wrap(['page.get_by_test_id("row").last.click()']))
    assert "nth=-1" in r.markdown


# ── navigate + base-URL stripping ────────────────────────────────────────────


def test_navigate_strips_matching_origin():
    r = _tx(_wrap(['page.goto("https://a.co/projects?tab=1")']), base_url="https://a.co")
    assert "- action: navigate" in r.markdown
    assert "- target: /projects?tab=1" in r.markdown


def test_navigate_keeps_foreign_origin():
    r = _tx(_wrap(['page.goto("https://other.co/x")']), base_url="https://a.co")
    assert "- target: https://other.co/x" in r.markdown


# ── Lossy locators are flagged for review ────────────────────────────────────


def test_get_by_text_is_text_selector_with_note():
    r = _tx(_wrap(['page.get_by_text("Delete").click()']))
    assert "- selector: text=Delete" in r.markdown
    assert any("get_by_text" in n for n in r.notes)


def test_get_by_label_is_aria_label_with_note():
    r = _tx(_wrap(['page.get_by_label("City").fill("Gent")']))
    assert '- selector: [aria-label="City"]' in r.markdown
    assert any("get_by_label" in n for n in r.notes)


# ── expect(...) → assert / wait-for ──────────────────────────────────────────


def test_expect_visible():
    r = _tx(_wrap(['expect(page.get_by_test_id("panel")).to_be_visible()']))
    assert "- action: assert" in r.markdown
    assert '- expect: visible([data-testid="panel"])' in r.markdown


def test_expect_hidden_maps_to_hidden():
    r = _tx(_wrap(['expect(page.get_by_test_id("spinner")).to_be_hidden()']))
    assert '- expect: hidden([data-testid="spinner"])' in r.markdown


def test_expect_contains_text():
    r = _tx(_wrap(['expect(page.get_by_test_id("toast")).to_contain_text("Saved")']))
    assert '- expect: contains([data-testid="toast"], "Saved")' in r.markdown


def test_expect_url_stripped():
    r = _tx(_wrap(['expect(page).to_have_url("https://a.co/done")']), base_url="https://a.co")
    assert '- expect: url-contains("/done")' in r.markdown


def test_expect_unknown_matcher_dropped_with_note():
    r = _tx(_wrap(['expect(page.get_by_test_id("x")).to_have_attribute("data-y", "1")']))
    assert "- action: assert" not in r.markdown
    assert any("dropped" in n for n in r.notes)


# ── Unknown / unsupported statements degrade to custom (never crash) ─────────


def test_unknown_call_becomes_custom():
    r = _tx(_wrap(["page.mouse.wheel(0, 200)"]))
    assert "- action: custom" in r.markdown
    assert "- code: |" in r.markdown
    assert any("unrecognized" in n for n in r.notes)


def test_with_block_becomes_custom():
    src = (
        "from playwright.sync_api import Page\n\n\n"
        "def test_example(page: Page) -> None:\n"
        "    with page.expect_popup() as info:\n"
        '        page.get_by_test_id("open").click()\n'
    )
    r = _tx(src)
    assert "- action: custom" in r.markdown
    assert "with page.expect_popup()" in r.markdown


def test_empty_recording_zero_steps():
    src = "from playwright.sync_api import Page\n\n\ndef test_example(page: Page) -> None:\n    pass\n"
    r = _tx(src)
    assert r.step_count == 0


# ── Round-trip: the draft validates AND generates valid Python ───────────────


def test_roundtrip_parses_and_generates(tmp_path):
    src = _wrap(
        [
            'page.goto("https://a.co/projects")',
            'page.get_by_test_id("new").click()',
            'page.get_by_test_id("title").fill("Redesign")',
            'page.get_by_role("combobox", name="Owner").select_option("me")',
            'expect(page.get_by_test_id("toast")).to_contain_text("Created")',
        ]
    )
    r = _tx(src, base_url="https://a.co")
    path = tmp_path / "recorded.md"
    path.write_text(r.markdown, encoding="utf-8")

    scenario = parse_scenario_file(path)  # full pydantic validation
    assert [s.action for s in scenario.steps] == [
        "navigate",
        "click",
        "fill",
        "select",
        "assert",
    ]
    # ...and codegen produces valid Python (proves required fields are present).
    generated = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(generated)


# ── Runtime _interp_val: resolves ${VAR} in recorded fill values ─────────────

_CONFTEST = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"
_TEST_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "test_scenario.py.j2"


def _extract(start: str, end: str) -> str:
    text = _CONFTEST.read_text(encoding="utf-8")
    s = text.index(start)
    e = text.index(end, s)
    return text[s:e]


def _load_interp_val():
    import os

    ns = {
        "os": os,
        "re": re,
        "_ENV_REF": re.compile(r"\$\{([A-Za-z_]\w*)(?::-([^}]*))?\}"),
    }
    # _interp lives inside the {% raw %} block; _interp_val just after {% endraw %}.
    exec(compile(_extract("def _interp(s, _vars):", "{% endraw %}"), "<interp>", "exec"), ns)
    exec(compile(_extract("def _interp_val(s, _vars):", "def _resolve_date_obj("), "<interp_val>", "exec"), ns)
    return ns["_interp_val"]


def test_interp_val_resolves_env(monkeypatch):
    f = _load_interp_val()
    monkeypatch.setenv("STEPPY_ADMIN_TOKEN", "sekret")
    assert f("${STEPPY_ADMIN_TOKEN}", {}) == "sekret"


def test_interp_val_uses_default_when_unset(monkeypatch):
    f = _load_interp_val()
    monkeypatch.delenv("STEPPY_NOPE", raising=False)
    assert f("${STEPPY_NOPE:-fallback}", {}) == "fallback"


def test_interp_val_leaves_unset_literal(monkeypatch):
    f = _load_interp_val()
    monkeypatch.delenv("STEPPY_NOPE", raising=False)
    assert f("${STEPPY_NOPE}", {}) == "${STEPPY_NOPE}"


def test_interp_val_env_wins_over_default(monkeypatch):
    f = _load_interp_val()
    monkeypatch.setenv("STEPPY_ADMIN_TOKEN", "envval")
    assert f("${STEPPY_ADMIN_TOKEN:-def}", {}) == "envval"


def test_interp_val_still_does_braces():
    f = _load_interp_val()
    assert f("{{x}}", {"x": "y"}) == "y"


def test_selector_site_keeps_plain_interp():
    """The value arg uses _interp_val; the selector arg stays _interp (byte-safe)."""
    tpl = _TEST_TEMPLATE.read_text(encoding="utf-8")
    # Matched without the closing paren: `timeout:`/`force:` append optional
    # kwargs after the value argument. What this pins is which interpolator
    # guards which argument, not the rest of the call.
    assert (
        "_page.fill(_interp({{ step.selector | pystr }}, _vars), _interp_val({{ step.value | pystr }}, _vars)"
    ) in tpl
    assert (
        "_page.select_option(_interp({{ step.selector | pystr }}, _vars), _interp_val({{ step.value | pystr }}, _vars)"
    ) in tpl
