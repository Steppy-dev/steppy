"""The generated in-app tour replay (``test_<slug>__tour``).

The load-bearing property: a replay performs each step with **exactly** the
code the scenario test runs for it (the ``step_body`` macro), minus the guide
captures, plus tour checks around it. Byte identity of tours-off output is
proven separately by ``tests/_codegen_snapshot.py``.
"""

import ast
import re
import textwrap
from pathlib import Path

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.models import SteppyConfig
from steppy.parser import parse_scenario_file, parse_scenarios_from_directory
from steppy.tours.compiler import compile_tours
from steppy.tours.replay import ReplayPlanError, build_replay
from steppy.tours.settings import ToursSettings

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
DEMO = ROOT / "fixtures" / "tour-demo" / "project"


def _demo():
    config = load_config(DEMO / "config.yaml")
    scenarios = parse_scenarios_from_directory(DEMO / "scenarios")
    result = compile_tours(scenarios, ToursSettings.from_config(config, DEMO))
    assert not result.has_errors
    return config, {s.slug: s for s in scenarios}, result.replay_plans


def _replay_section(code: str) -> str:
    return code[code.index("# The in-app tour this scenario compiles to") :]


@pytest.mark.parametrize("slug", ["editor--create-invoice", "editor--send-invoice"])
def test_the_replay_is_valid_python_with_the_expected_shape(slug):
    config, scenarios, plans = _demo()
    generator = SteppyGenerator(config, templates_dir=TEMPLATES)
    code = generator.generate_test_file(scenarios[slug], tour_plan=plans[slug])
    tree = ast.parse(code)
    functions = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    name = f"test_{slug.replace('-', '_')}__tour"
    assert functions[-1] == name and f"test_{slug.replace('-', '_')}" in functions
    replay = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    assert [a.arg for a in replay.args.args] == ["editor_page", "request"]
    assert f"{name}._steppy_tour_role = 'editor'" in code
    assert "from ._steppy_tours import TourProbe" in code
    assert code.endswith("\n") and not code.endswith("\n\n")
    section = _replay_section(code)
    for capture in ("take_screenshot", "take_fillform", "_safe_bbox", "_bbox"):
        assert capture not in section
    assert section.count("_steppy_mark_step(") == section.count("_steppy_end_step(")
    assert section.count("_tour_start(") == 1 and section.count("_tour_expect_complete(") == 1
    spec = ast.literal_eval(re.search(r"^TOUR = (.*?)\n\n\n", section, re.S | re.M).group(1))
    assert [s["id"] for s in spec["steps"]] == [t.step_id for e in plans[slug].entries for t in e.tour_steps]


def test_every_replayed_action_is_the_scenario_test_code(tmp_path):
    config, scenarios, plans = _demo()
    generator = SteppyGenerator(config, templates_dir=TEMPLATES)
    for slug, plan in plans.items():
        replay = build_replay(scenarios[slug], plan, config)
        code = generator.generate_test_file(scenarios[slug], tour_plan=plan)
        section = _replay_section(code)
        # Render each step copy with the same macro the main loop uses and
        # require its code verbatim in the replay.
        template = generator.env.get_template("test_scenario.py.j2")
        module = template.make_module(
            {"scenario": scenarios[slug], "config": config, "roles_needed": ["editor"], "tour_replay": None}
        )
        for item in replay.items:
            steps = [p for p, _c in item.parts] if item.parts else ([item.step] if item.step is not None else [])
            for step in steps:
                body = [line.strip() for line in str(module.step_body(step)).split("\n") if line.strip()]
                if body:
                    assert _contains_lines(section, body), (slug, step.step_number)


def _contains_lines(text: str, lines: list[str]) -> bool:
    """``lines`` (indentation ignored) appear contiguously in ``text``."""
    haystack = [line.strip() for line in text.split("\n") if line.strip()]
    n = len(lines)
    return any(haystack[i : i + n] == lines for i in range(len(haystack) - n + 1))


def _config(**tours):
    return SteppyConfig(
        apps={"editor": {"name": "E", "url": "http://localhost:3000", "auth": {"mode": "bypass"}}},
        locales=["en"],
        default_locale="en",
        tours={"enabled": True, "targets": [{"type": "guide"}], **tours},
    )


def _render(tmp_path, body, frontmatter="tour: true"):
    path = tmp_path / "editor--case.md"
    path.write_text(f"---\ntitle: Case\n{frontmatter}\n---\n" + textwrap.dedent(body), encoding="utf-8")
    scenario = parse_scenario_file(path)
    config = _config()
    result = compile_tours([scenario], ToursSettings.from_config(config, tmp_path))
    assert not result.has_errors, [d.render() for d in result.diagnostics]
    generator = SteppyGenerator(config, templates_dir=TEMPLATES)
    return generator.generate_test_file(scenario, tour_plan=result.replay_plans["editor--case"])


OPEN = "\n### 1. Open\n- role: editor\n- action: navigate\n- target: /invoices\n"


def test_noauth_scenarios_alias_the_noauth_fixtures(tmp_path):
    code = _render(
        tmp_path,
        OPEN + "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n",
        frontmatter="auth: false\ntour: true",
    )
    section = _replay_section(code)
    assert "(editor_page_noauth: Page, request):" in section and "    editor_page = editor_page_noauth" in section


def test_a_ranged_replay_starts_in_place_and_runs_the_rest_after_completion(tmp_path):
    code = _render(
        tmp_path,
        OPEN
        + "\n### 2. Customers\n- role: editor\n- action: click\n- selector: '[data-testid=\"nav-customers\"]'\n"
        + "\n### 3. Remove\n- role: editor\n- action: click\n- selector: '[data-testid=\"remove-btn\"]'\n"
        + "- tour: [start, end]\n"
        + "\n### 4. Undo\n- role: editor\n- action: click\n- selector: '[data-testid=\"undo-btn\"]'\n",
        frontmatter="tour:\n  entry: /customers",
    )
    ast.parse(code)
    section = _replay_section(code)
    assert "'in_place': True" in section and "    _tour_start(_tour, {})\n" in section
    customers = section.index("editor_page.click(_interp('[data-testid=\"nav-customers\"]'")
    start = section.index("_tour_start(")
    complete = section.index("_tour_expect_complete(")
    assert customers < start < section.index("_tour_expect_step(_tour, 'remove-btn'") < complete
    assert complete < section.index("# ── Step 4: Undo — after the tour") < section.index("undo-btn")
    assert section.count("_steppy_mark_step(") == section.count("_steppy_end_step(") == 4


def test_a_next_click_is_deferred_past_a_key_press(tmp_path):
    code = _render(
        tmp_path,
        OPEN
        + "\n### 2. Search\n- role: editor\n- action: fill\n- selector: '[data-testid=\"q\"]'\n- value: acme\n"
        + "\n### 3. Submit\n- role: editor\n- action: keyboard\n- value: Enter\n"
        + "\n### 4. Open\n- role: editor\n- action: click\n- selector: '[data-testid=\"row\"]'\n",
    )
    section = _replay_section(code)
    fill = section.index("'acme'")
    enter = section.index("keyboard.press('Enter')")
    ensure = section.index("_tour_ensure_advanced(_tour, 'q')")
    assert fill < enter < ensure < section.index("_tour_expect_step(_tour, 'row'")


def test_layout_steps_keep_their_runtime_guard(tmp_path):
    path = tmp_path / "editor--case.md"
    path.write_text(
        "---\ntitle: Case\ntour: true\n---\n"
        + OPEN
        + "\n### 2. Burger\n- role: editor\n- action: click\n- selector: '[data-testid=\"burger\"]'\n- layouts:\n  - mobile\n",
        encoding="utf-8",
    )
    config = SteppyConfig(
        apps={"editor": {"name": "E", "url": "http://localhost:3000", "auth": {"mode": "bypass"}}},
        locales=["en"],
        default_locale="en",
        screenshot={"layouts": {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}},
        tours={
            "enabled": True,
            "targets": [{"type": "guide"}],
            "layouts": {"desktop": "(min-width: 768px)", "mobile": "(max-width: 767.98px)"},
        },
    )
    scenario = parse_scenario_file(path)
    result = compile_tours([scenario], ToursSettings.from_config(config, tmp_path))
    code = SteppyGenerator(config, templates_dir=TEMPLATES).generate_test_file(
        scenario, tour_plan=result.replay_plans["editor--case"]
    )
    section = _replay_section(code)
    assert "    if _step_runs(None, ['mobile']):\n        _steppy_mark_step(2" in section
    assert "'layouts': {'mobile': '(max-width: 767.98px)'}" in section


NASTY = ['It\'s "quoted"', "back\\slash", "café — {{x}}", "a'b\"c", "line\nbreak"]


@pytest.mark.parametrize("nasty", NASTY)
def test_nasty_strings_survive_into_valid_python(tmp_path, nasty):
    title = nasty.replace("\n", " ")
    code = _render(
        tmp_path,
        OPEN
        + f"\n### 2. {title}\n- role: editor\n- action: fill\n- selector: '[data-testid=\"name\"]'\n"
        + f"- value: {nasty.replace(chr(10), ' ')!r}\n",
    )
    tree = ast.parse(code)
    assert any(isinstance(n, ast.FunctionDef) and n.name.endswith("__tour") for n in tree.body)


def test_a_template_override_without_the_replay_section_fails_loudly(tmp_path):
    override = tmp_path / "templates"
    override.mkdir()
    text = (TEMPLATES / "test_scenario.py.j2").read_text(encoding="utf-8")
    cut = text[: text.index("{#\n  In-app tour replay")]
    (override / "test_scenario.py.j2").write_bytes(cut.encode())
    config, scenarios, plans = _demo()
    generator = SteppyGenerator(config, templates_dir=override)
    with pytest.raises(ValueError, match="predates in-app tours"):
        generator.generate_test_file(scenarios["editor--send-invoice"], tour_plan=plans["editor--send-invoice"])


def test_a_misaligned_plan_is_a_compiler_bug():
    config, scenarios, plans = _demo()
    plan = plans["editor--send-invoice"]
    broken = type(plan)(**{**plan.__dict__, "entries": plan.entries[:-1]})
    with pytest.raises(ReplayPlanError):
        build_replay(scenarios["editor--send-invoice"], broken, config)


def test_generate_all_writes_the_helper_only_with_tours_on(tmp_path):
    config, scenarios, plans = _demo()
    out = tmp_path / "tests" / "e2e"
    results = SteppyGenerator(config, templates_dir=TEMPLATES).generate_all(
        list(scenarios.values()), out, tour_plans=plans
    )
    helper = out / "_steppy_tours.py"
    assert results[str(helper)] == "generated"
    assert helper.read_text(encoding="utf-8") == (TEMPLATES / "_steppy_tours.py").read_text(encoding="utf-8")
    again = SteppyGenerator(config, templates_dir=TEMPLATES).generate_all(
        list(scenarios.values()), out, tour_plans=plans
    )
    assert again[str(helper)] == "unchanged"
    with pytest.raises(ValueError, match="tour plans"):
        SteppyGenerator(config, templates_dir=TEMPLATES).generate_all(list(scenarios.values()), out)

    plain = load_config(ROOT / "config.yaml")
    off = tmp_path / "off"
    SteppyGenerator(plain, templates_dir=TEMPLATES).generate_all(list(scenarios.values()), off)
    assert not (off / "_steppy_tours.py").exists()


def test_a_held_step_is_hovered_and_checked_after_its_popover_anchors(tmp_path):
    code = _render(
        tmp_path,
        OPEN
        + "\n### 2. Toggle\n- role: editor\n- action: click\n- selector: '[data-testid=\"toggle\"]'\n"
        + "- tour-hold: '[data-testid=\"side-nav\"]'\n",
    )
    ast.parse(code)
    assert ", _tour_hold\n" in code
    section = _replay_section(code)
    assert "'hold': 'side-nav'" in section
    expect = section.index("_tour_expect_step(_tour, 'toggle'")
    assert expect < section.index("_tour_hold(_tour, 'toggle')") < section.index("editor_page.click(")


def test_without_a_hold_the_replay_neither_imports_nor_calls_it(tmp_path):
    code = _render(
        tmp_path, OPEN + "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n"
    )
    assert "_tour_hold" not in code and "'hold'" not in code


def test_a_target_click_leaves_the_replay_unchanged(tmp_path):
    # The replay presses the bubble's Next on an information step and never its
    # target, so swallowing target clicks cannot change what CI does.
    info = (
        '\n### 2. Look\n- role: editor\n- action: assert\n- expect: visible([data-testid="create"])\n'
        '- guide-text:\n    en: "This button creates one."\n'
    )
    plain = _render(tmp_path, OPEN + info)
    swallowed = _render(tmp_path, OPEN + info + "- tour-target-click: next\n")
    assert swallowed == plain
