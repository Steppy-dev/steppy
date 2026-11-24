"""Screenshot masks (spec I7): ``mask:`` in front matter and on a step.

Two failure shapes matter here, and both are silent:

  * a mask that is read as something else masks nothing. ``mask: [data-testid="iban"]``
    is the trap: YAML reads a list holding ``data-testid="iban"``, a selector
    Playwright accepts and matches with nothing;
  * a capture that forgets the masks. The failure screenshot did, for every
    project that had configured ``screenshot.mask``.

So every spelling is pinned to the selector it means or to an error, and the
failure capture to "masked, or not taken". The pixels themselves are checked in
Chromium by ``tests/integration/test_screenshot_mask_it.py``.
"""

import ast
import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.masks import MaskSyntaxError, merge_masks, normalize_masks
from steppy.migrate import apply_plan, plan_file
from steppy.models import _ALLOWED_FIELDS, AppAuthConfig, AppConfig, Scenario, ScreenshotConfig, SteppyConfig, Step
from steppy.parser import ParseError, lint_scenario_selectors, parse_scenario_file, parse_scenarios_from_directory
from steppy.record_cli import codegen_source_to_scenario
from tests.test_step_events_codegen import _ACTION_BODIES

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

IBAN = '[data-testid="iban"]'


def _scenario(tmp_path, *, front: str = "", step: str = "", name: str = "demo.md") -> Scenario:
    path = tmp_path / name
    front = f"{front}\n" if front else ""
    step = f"{step}\n" if step else ""
    path.write_text(
        f"---\ntitle: Demo\nauth: false\n{front}tags: [smoke]\n---\n\n"
        f"### 1. Open\n- role: editor\n- action: navigate\n- target: /\n- screenshot: editor\n{step}"
        "\n### 2. Click\n- role: editor\n- action: click\n- selector: '#go'\n- screenshot: editor\n",
        encoding="utf-8",
    )
    return parse_scenario_file(path)


def _generator(**screenshot) -> SteppyGenerator:
    config = SteppyConfig(
        apps={"editor": AppConfig(name="N", url="http://x", auth=AppAuthConfig(mode="bypass"))},
        screenshot=ScreenshotConfig(**screenshot),
    )
    return SteppyGenerator(config, templates_dir=TEMPLATES)


# ── What a step's `- mask:` means ────────────────────────────────────────────

_STEP_SPELLINGS = {
    # One selector, as written: the same reading as `- selector:`.
    f"- mask: {IBAN}": [IBAN],
    "- mask: [data-testid='iban']": ["[data-testid='iban']"],
    "- mask: [data-testid=iban]": ["[data-testid=iban]"],
    "- mask: [data-private]": ["[data-private]"],
    '- mask: [title="a, b # c" i]': ['[title="a, b # c" i]'],
    '- mask: [data-testid="card"] .name': ['[data-testid="card"] .name'],
    '- mask: [data-testid="a"], [data-testid="b"]': ['[data-testid="a"], [data-testid="b"]'],
    "- mask: .customer-name": [".customer-name"],
    "- mask: #iban": ["#iban"],
    "- mask: .card #iban": [".card #iban"],
    "- mask: *[data-private]": ["*[data-private]"],
    "- mask: input[type='password']": ["input[type='password']"],
    "- mask: .a, .b": [".a, .b"],
    '- mask: td:has-text("IBAN: BE")': ['td:has-text("IBAN: BE")'],
    '- mask: text="Jane Doe"': ['text="Jane Doe"'],
    '- mask: xpath=//td[@id="iban"]': ['xpath=//td[@id="iban"]'],
    # Quoted.
    "- mask: '[data-testid=\"iban\"]'": [IBAN],
    '- mask: "[data-testid=\\"iban\\"]"': [IBAN],
    '- mask: "[data-testid="iban"]"': [IBAN],
    "- mask: 'text=\"Jane Doe\"'": ['text="Jane Doe"'],
    # A list: quoted entries inline, or one selector per indented line.
    "- mask: ['.a', \".b\"]": [".a", ".b"],
    "- mask: [ '[data-testid=\"iban\"]' , '.b', ]": [IBAN, ".b"],
    f"- mask:\n    - {IBAN}\n    - .customer-name": [IBAN, ".customer-name"],
    "- mask:\n    - '.a'\n    - input[type='password']  # the login field": [".a", "input[type='password']"],
    f"- mask:\n    - {IBAN}  # the account\n    - *[data-private]": [IBAN, "*[data-private]"],
    '- mask:\n    [\n      ".a",\n      ".b",\n    ]': [".a", ".b"],
    "- mask:\n    - .a\n    - .b\n    - .a": [".a", ".b"],
}


@pytest.mark.parametrize("written", _STEP_SPELLINGS)
def test_every_step_spelling_is_the_selector_it_looks_like(tmp_path, written):
    assert _scenario(tmp_path, step=written).steps[0].mask == _STEP_SPELLINGS[written]


@pytest.mark.parametrize(
    "written",
    [
        "- mask:",
        "- mask: ''",
        '- mask: ""',
        "- mask: []",
        "- mask: [ ]",
        "- mask: ['']",
        "- mask: true",
        "- mask: 12",
        "- mask: ~",
        "- mask: {a: b}",
        "- mask: [.a, .b]",
        "- mask: [input, p]",
        '- mask: [data-testid="a", data-testid="b"]',
        "- mask: ['.a', .b]",
        "- mask:\n    - ['.a', '.b']",
        "- mask:\n    - [.a, .b]",
        "- mask:\n    - #iban",
        "- mask:\n    - .card #iban",
        "- mask:\n    - 12",
        "- mask:\n    - ''",
        "- mask:\n    - .a\n    -",
        "- mask:\n    a: b",
        "- mask: |\n    .a\n    .b",
        '- mask:\n    [data-testid="a",\n     data-testid="b"]',
        '- mask: data-testid="iban"',
        '- mask: [data-testid="iban"]  # the account',
        "- mask: .customer-name  # who it is",
        "- mask: .a\n- mask: .b",
        "- mask:\n    - .a\n- mask:\n    - .b",
        '- mask: [data-id="{{id}}"]',
        "- mask: .row-${ID}",
    ],
)
def test_a_doubtful_step_mask_is_an_error_not_a_guess(tmp_path, written):
    with pytest.raises(ParseError, match="(?i)mask"):
        _scenario(tmp_path, step=written)


# ── What a front matter's `mask:` means ──────────────────────────────────────

_FRONT_SPELLINGS = {
    f"mask: {IBAN}": [IBAN],
    f"mask: {IBAN}  # the account number": [IBAN],
    "mask: [data-testid='iban']": ["[data-testid='iban']"],
    "mask: [data-testid=iban]": ["[data-testid=iban]"],
    "mask: [data-private]": ["[data-private]"],
    'mask: [title="a, b # c" i]': ['[title="a, b # c" i]'],
    'mask: [data-testid="card"] .name': ['[data-testid="card"] .name'],
    '"mask": .customer-name': [".customer-name"],
    "mask : [data-private]": ["[data-private]"],
    "'mask': [data-private]": ["[data-private]"],
    "mask: .customer-name": [".customer-name"],
    "mask: .customer-name  # who it is": [".customer-name"],
    "mask: '#iban'": ["#iban"],
    "mask: *[data-private]": ["*[data-private]"],
    'mask: td:has-text("IBAN: BE")': ['td:has-text("IBAN: BE")'],
    "mask: '[data-testid=\"iban\"]'": [IBAN],
    "mask: ['.a', \".b\"]": [".a", ".b"],
    'mask: [\n  ".a",\n  ".b",\n]': [".a", ".b"],
    f"mask:\n  - {IBAN}\n  - .customer-name": [IBAN, ".customer-name"],
    # `steppy record` writes the list unindented (yaml.safe_dump).
    f"mask:\n- {IBAN}\n- input[type='password']": [IBAN, "input[type='password']"],
    f"mask:  # why\n\n  # the account\n  - {IBAN}  # it\n  - '.b'\ndescription: a [b] c": [IBAN, ".b"],
}


@pytest.mark.parametrize("written", _FRONT_SPELLINGS)
def test_every_front_matter_spelling_is_the_selector_it_looks_like(tmp_path, written):
    scenario = _scenario(tmp_path, front=written)
    assert scenario.mask == _FRONT_SPELLINGS[written]
    # The rest of the header is read as before.
    assert scenario.tags == ["smoke"] and scenario.auth is False


@pytest.mark.parametrize("written", _FRONT_SPELLINGS)
def test_front_matter_spellings_survive_windows_line_endings(tmp_path, written):
    _scenario(tmp_path, front=written)
    path = tmp_path / "demo.md"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert parse_scenario_file(path).mask == _FRONT_SPELLINGS[written]


@pytest.mark.parametrize(
    "written",
    [
        "mask:",
        "mask: ~",
        "mask: ''",
        "mask: []",
        "mask: ['']",
        "mask: true",
        "mask: 12",
        "mask: {a: b}",
        "mask: #iban",
        "mask: .card #iban",
        "mask: [.a, .b]",
        "mask: [input, p]",
        'mask: [data-testid="a", data-testid="b"]',
        "mask: ['.a', .b]",
        'mask: [data-testid="a",\n  data-testid="b"]',
        "mask: .a\nmask: .b",
        "mask:\n  - .a\npriority: high\nmask: .b",
        "mask:\n  - #iban",
        "mask:\n  - .card #iban",
        "mask:\n  - 12",
        "mask:\n  - ['.a', '.b']",
        "mask:\n  a: b",
        'mask:\n  - td:has-text("IBAN: BE")',
        "mask: |\n  .a\n  .b",
        'mask: [data-id="{{id}}"]',
    ],
)
def test_a_doubtful_front_matter_mask_is_an_error_not_a_guess(tmp_path, written):
    with pytest.raises(ParseError, match="(?i)mask"):
        _scenario(tmp_path, front=written)


def test_a_mask_key_inside_another_block_is_left_alone(tmp_path):
    """Only the top-level key is ours: a `mask:` elsewhere keeps YAML's reading."""
    with pytest.raises(ParseError, match="guide"):
        _scenario(tmp_path, front="guide:\n  mask: [data-a]")


def test_a_scenario_without_a_mask_is_read_as_before(tmp_path):
    scenario = _scenario(tmp_path)
    assert scenario.mask is None and scenario.declared_masks() == []
    assert all(step.mask is None for step in scenario.steps)


# ── The models ───────────────────────────────────────────────────────────────


def test_the_rules_are_the_same_on_both_models():
    for build in (
        lambda mask: Step(action="login", role="editor", mask=mask),
        lambda mask: Scenario(title="T", mask=mask),
    ):
        assert build(IBAN).mask == [IBAN]
        assert build([IBAN, " .b ", IBAN]).mask == [IBAN, ".b"]
        for bad in (None, "", "  ", [], [""], [None], [1], True, 3, {"a": "b"}):
            with pytest.raises(ValidationError, match="mask"):
                build(bad)


def test_the_error_says_what_to_write():
    with pytest.raises(MaskSyntaxError, match="remove the key"):
        normalize_masks([])
    with pytest.raises(MaskSyntaxError, match="quote"):
        normalize_masks([["a"]])
    with pytest.raises(MaskSyntaxError, match=re.escape("'[data-testid=\"iban\"]'")):
        normalize_masks('data-testid="iban"')


@pytest.mark.parametrize("action", sorted(_ALLOWED_FIELDS))
def test_every_action_accepts_a_mask(tmp_path, action):
    """Even one that takes no screenshot: its mask counts for the failure one."""
    path = tmp_path / "s.md"
    path.write_text(
        "---\ntitle: T\nauth: false\n---\n\n### 1. S\n- role: editor\n"
        f"- action: {action}\n- mask: {IBAN}\n{_ACTION_BODIES[action]}\n",
        encoding="utf-8",
    )
    assert parse_scenario_file(path).steps[0].mask == [IBAN]


def test_the_project_mask_refuses_what_would_mask_nothing():
    assert ScreenshotConfig(mask=[]).mask == []
    # An unset ${VAR} stays as written, with the loader's own warning.
    assert ScreenshotConfig(mask=["${MASKED}"]).mask == ["${MASKED}"]
    for bad in ([""], ["  "], ['data-testid="iban"']):
        with pytest.raises(ValidationError, match="mask"):
            ScreenshotConfig(mask=bad)


def test_the_grammar_is_a_stdlib_only_leaf():
    """`models.py` imports it, on the path of every `steppy` command."""
    tree = ast.parse((ROOT / "src" / "steppy" / "masks.py").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


def test_merge_keeps_order_and_drops_duplicates():
    assert merge_masks([".a", ".b"], None, [], [".b", ".c"]) == [".a", ".b", ".c"]


def test_a_brittle_mask_is_linted_like_a_selector(tmp_path):
    scenario = _scenario(tmp_path, front="mask: div > span", step="- mask: tr:nth-child(2)")
    warnings = [w for w in lint_scenario_selectors(scenario) if "mask" in w]
    assert len(warnings) == 2
    assert warnings[0].startswith("scenario: brittle mask 'div > span'")
    assert warnings[1].startswith("step 1: brittle mask 'tr:nth-child(2)'")


# ── `steppy validate` / `steppy generate` ────────────────────────────────────

_CONFIG = """\
apps:
  editor:
    name: "Demo"
    url: "http://127.0.0.1:9"
    auth: {mode: bypass}
"""


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    (tmp_path / "config.yaml").write_text(_CONFIG, encoding="utf-8")
    (tmp_path / "scenarios").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _invoke(*args):
    from steppy.cli import app

    result = CliRunner().invoke(app, list(args))
    return result.exit_code, " ".join(re.sub(r"\x1b\[[0-9;]*m", "", result.output).split())


@pytest.mark.parametrize("command", ["validate", "generate"])
@pytest.mark.parametrize(
    "front, step",
    [("mask: []", ""), ("", "- mask: [.a, .b]"), ("mask: 12", ""), ("", "- mask:\n    - 12")],
)
def test_the_commands_refuse_a_bad_mask(project, command, front, step):
    (project / "scenarios" / "demo.md").write_text(
        f"---\ntitle: T\n{front}\n---\n\n### 1. S\n- role: editor\n- action: login\n{step}\n", encoding="utf-8"
    )
    code, output = _invoke(command)
    assert code == 1 and "mask" in output
    assert not (project / "tests" / "e2e" / "test_demo.py").exists()


# ── Generated code ───────────────────────────────────────────────────────────


def _markers(code: str) -> dict[int, list[str]]:
    """Step number → the ``mask=`` its marker carries."""
    found = {}
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_steppy_mark_step":
            keywords = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
            found[ast.literal_eval(node.args[0])] = keywords.get("mask", [])
    return found


def _scenario_mask(code: str):
    for node in ast.parse(code).body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "SCENARIO_MASK":
            return ast.literal_eval(node.value)
    return None


def test_a_step_captures_with_the_scenarios_masks_then_its_own(tmp_path):
    scenario = _scenario(tmp_path, front="mask: ['.a', '.b']", step=f"- mask:\n    - .b\n    - {IBAN}")
    code = _generator().generate_test_file(scenario)
    assert _markers(code) == {1: [".a", ".b", IBAN], 2: [".a", ".b"]}
    assert _scenario_mask(code) == [".a", ".b", IBAN]


def test_a_step_mask_stays_on_its_step_but_counts_for_the_failure(tmp_path):
    code = _generator().generate_test_file(_scenario(tmp_path, step=f"- mask: {IBAN}"))
    assert _markers(code) == {1: [IBAN], 2: []}
    assert _scenario_mask(code) == [IBAN]


def test_the_parsed_scenario_is_not_changed_by_generating(tmp_path):
    scenario = _scenario(tmp_path, front="mask: .a", step=f"- mask: {IBAN}")
    _generator().generate_test_file(scenario)
    assert scenario.steps[0].mask == [IBAN] and scenario.steps[1].mask is None


def test_no_mask_generates_what_it_always_did(tmp_path):
    """The project's own mask is read at run time, so it changes no generated test."""
    scenario = _scenario(tmp_path)
    code = _generator().generate_test_file(scenario)
    assert "mask=" not in code and "SCENARIO_MASK" not in code
    assert code == _generator(mask=[".pii"], mask_color="#123456").generate_test_file(scenario)


def test_a_mask_adds_the_union_and_the_markers_and_nothing_else(tmp_path):
    """Every other generated byte stays where it was."""
    plain = _generator().generate_test_file(_scenario(tmp_path))
    masked = _generator().generate_test_file(_scenario(tmp_path, front="mask: .a"))
    back = re.sub(r"# Every mask of this scenario.*\nSCENARIO_MASK = .*\n", "", masked)
    assert back.replace(", mask=['.a'])", ")") == plain


def test_a_conditional_step_keeps_its_mask_inside_the_guard(tmp_path):
    code = _generator().generate_test_file(
        _scenario(tmp_path, step=f"- mask: {IBAN}\n- only-in: guide\n- optional: true")
    )
    ast.parse(code)
    assert _markers(code)[1] == [IBAN]


def test_the_tour_replay_carries_the_masks_too(tmp_path):
    """A failing replay takes the same failure screenshot as the scenario test."""
    import shutil

    from steppy.tours.compiler import compile_tours
    from steppy.tours.settings import ToursSettings

    demo = ROOT / "fixtures" / "tour-demo" / "project"
    shutil.copytree(demo / "scenarios", tmp_path / "scenarios")
    path = tmp_path / "scenarios" / "editor--create-invoice.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("---\n", f"---\nmask: {IBAN}\n", 1), encoding="utf-8")
    config = load_config(demo / "config.yaml")
    scenarios = parse_scenarios_from_directory(tmp_path / "scenarios")
    plans = compile_tours(scenarios, ToursSettings.from_config(config, demo)).replay_plans
    scenario = next(s for s in scenarios if s.slug == "editor--create-invoice")
    code = SteppyGenerator(config, templates_dir=TEMPLATES).generate_test_file(scenario, tour_plan=plans[scenario.slug])
    ast.parse(code)
    assert _scenario_mask(code) == [IBAN]
    assert code.count("_steppy_mark_step(") == code.count(f"mask=[{IBAN!r}])") > len(scenario.steps)


# ── Project-local template overrides ─────────────────────────────────────────


def _override(tmp_path, name: str, edit) -> SteppyGenerator:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / name).write_text(edit((TEMPLATES / name).read_text(encoding="utf-8")), encoding="utf-8")
    return SteppyGenerator(_generator(mask=[".pii"]).config, project_root=tmp_path)


def test_a_test_override_that_would_drop_the_masks_is_refused(tmp_path):
    generator = _override(tmp_path, "test_scenario.py.j2", lambda t: t.replace("SCENARIO_MASK = ", "# "))
    assert generator.generate_test_file(_scenario(tmp_path))
    with pytest.raises(ValueError, match="test_scenario.py.j2 override predates scenario masks"):
        generator.generate_test_file(_scenario(tmp_path, front="mask: .a"))


def test_a_test_override_without_the_step_markers_is_refused(tmp_path):
    generator = _override(tmp_path, "test_scenario.py.j2", lambda t: t.replace(" | bracket_step(step)", ""))
    with pytest.raises(ValueError, match="override predates scenario masks"):
        generator.generate_test_file(_scenario(tmp_path, step="- mask: .a"))


def test_a_conftest_override_that_would_drop_the_masks_is_refused(tmp_path):
    generator = _override(tmp_path, "conftest.py.j2", lambda t: t.replace("SCENARIO_MASK", "OLD"))
    with pytest.raises(ValueError, match="conftest.py.j2 override predates scenario masks"):
        generator.generate_conftest([_scenario(tmp_path, front="mask: .a")])


def test_a_conftest_override_with_only_a_project_mask_is_a_warning(tmp_path):
    """Its step captures are masked; its failure captures are what they always were."""
    generator = _override(tmp_path, "conftest.py.j2", lambda t: t.replace("SCENARIO_MASK", "OLD"))
    assert generator.generate_conftest([_scenario(tmp_path)])
    assert len(generator.warnings) == 1 and "screenshot.mask does not apply" in generator.warnings[0]


def test_generate_prints_that_warning(project):
    (project / "config.yaml").write_text(_CONFIG + "screenshot:\n  mask: ['.pii']\n", encoding="utf-8")
    _scenario(project / "scenarios")
    templates = project / "templates"
    templates.mkdir()
    text = (TEMPLATES / "conftest.py.j2").read_text(encoding="utf-8").replace("SCENARIO_MASK", "OLD")
    (templates / "conftest.py.j2").write_text(text, encoding="utf-8")
    code, output = _invoke("generate")
    assert code == 0 and "WARNING your templates/conftest.py.j2 override predates" in output


def test_the_bundled_templates_warn_about_nothing(tmp_path):
    generator = _generator(mask=[".pii"])
    generator.generate_conftest([_scenario(tmp_path, front="mask: .a")])
    assert generator.warnings == []


# ── The generated conftest ───────────────────────────────────────────────────


@pytest.fixture
def conftest(tmp_path, monkeypatch):
    """Load the real generated conftest, with the given ``screenshot:`` settings."""

    def load(**screenshot):
        root = tmp_path / "generated"
        (root / "tests" / "e2e").mkdir(parents=True, exist_ok=True)
        (root / "config.yaml").write_text(_CONFIG + f"screenshot: {json.dumps(screenshot)}\n", encoding="utf-8")
        path = root / "tests" / "e2e" / "conftest.py"
        path.write_text(_generator().generate_conftest([_scenario(tmp_path)]), encoding="utf-8")
        for name in ("STEPPY_EMIT_EVENTS", "STEPPY_EVENTS_DIR", "STEPPY_STEP_EVENTS"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("STEPPY_SCREENSHOTS", "1")
        spec = importlib.util.spec_from_file_location("steppy_mask_conftest", path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "steppy_mask_conftest", module)
        spec.loader.exec_module(module)
        return module

    return load


class _Page:
    """Records what each screenshot masked, and fails when told to."""

    url = "http://x/"
    viewport_size = {"width": 800, "height": 600}

    def __init__(self, error=None, partial=False):
        self.calls = []
        self.error = error
        self.partial = partial

    def locator(self, selector):
        return types.SimpleNamespace(selector=selector)

    def title(self):
        return "T"

    def screenshot(self, path, mask=(), **kwargs):
        self.calls.append({"mask": [m.selector for m in mask], **kwargs})
        if self.error:
            if self.partial:
                Path(path).write_bytes(b"half")
            raise self.error
        Path(path).write_bytes(b"png")


def test_a_step_capture_is_masked_with_the_project_then_the_step(conftest):
    module = conftest(mask=[".pii", ".a"], mask_color="#123456")
    page = _Page()
    module._steppy_mark_step(1, "Open", "navigate", mask=[".a", IBAN])
    module.take_screenshot_with_bbox(page, "demo", 1, "navigate")
    module.take_screenshot(page, "demo", 1)
    assert [c["mask"] for c in page.calls] == [[".pii", ".a", IBAN]] * 2
    assert {c["mask_color"] for c in page.calls} == {"#123456"}


def test_the_next_step_drops_the_previous_steps_masks(conftest):
    module = conftest(mask=[".pii"])
    page = _Page()
    module._steppy_mark_step(1, "Open", "navigate", mask=[IBAN])
    module._steppy_end_step(1)
    module._steppy_mark_step(2, "Click", "click")
    module.take_screenshot_with_bbox(page, "demo", 2, "click")
    assert page.calls[0]["mask"] == [".pii"]


def test_nothing_masked_passes_no_mask_arguments(conftest):
    module = conftest()
    page = _Page()
    module._steppy_mark_step(1, "Open", "navigate")
    module.take_screenshot(page, "demo", 1)
    assert page.calls == [{"mask": []}]


def test_one_project_mask_written_as_text_is_one_selector(conftest):
    """`steppy run` reads config.yaml raw: iterating the text would mask <i>, <n>, <p>…"""
    page = _Page()
    assert [m.selector for m in conftest(mask="input")._screenshot_mask_kwargs(page)["mask"]] == ["input"]


def test_every_fill_form_frame_is_masked(conftest):
    module = conftest(mask=[".pii"])
    page = _Page()
    page.locator = lambda selector: MagicMock(selector=selector, **{"first.bounding_box.return_value": None})
    module._steppy_mark_step(3, "Fill", "fill-form", mask=[IBAN])
    module.take_fillform_progressive_frames(page, "demo", 3, [("#a", "1"), ("#b", "2")])
    assert [c["mask"] for c in page.calls] == [[".pii", IBAN]] * 3


# ── Failure captures ─────────────────────────────────────────────────────────


def _fail(module, pages: dict, scenario_mask=None):
    """Drive the real hook for a failed call phase; returns nothing, like pytest."""
    attrs = {"SCENARIO_MASK": scenario_mask} if scenario_mask is not None else {}
    item = types.SimpleNamespace(
        nodeid="tests/e2e/test_demo.py::test_demo[fr-mobile]",
        funcargs=dict(pages),
        module=types.SimpleNamespace(**attrs),
    )
    hook = module.pytest_runtest_makereport(item, None)
    next(hook)
    outcome = types.SimpleNamespace(get_result=lambda: types.SimpleNamespace(when="call", failed=True))
    with pytest.raises(StopIteration):
        hook.send(outcome)


def _failure_files(module) -> list[str]:
    return sorted(p.name for p in (module.SCREENSHOTS_DIR / "_failures").glob("*"))


def test_a_failure_capture_takes_every_mask_on_every_role_page(conftest, capsys):
    module = conftest(mask=[".pii"], mask_color="#123456")
    module._steppy_mark_step(2, "Click", "click", mask=[".b"])  # the step that failed
    pages = {"editor_page": _Page(), "reviewer_page_noauth": _Page(), "other": object()}
    _fail(module, pages, scenario_mask=[".a", IBAN, ".b"])
    for name in ("editor_page", "reviewer_page_noauth"):
        assert pages[name].calls == [
            {"mask": [".pii", ".a", IBAN, ".b"], "mask_color": "#123456", "full_page": True, "timeout": 5000}
        ]
    assert len(_failure_files(module)) == 2
    assert capsys.readouterr().out.count("  screenshot: ") == 2


def test_a_failure_capture_without_a_scenario_mask_takes_the_projects(conftest):
    module = conftest(mask=[".pii"])
    # A stale step mask of another test must not be what decides here.
    module._steppy_mark_step(9, "Earlier test", "click", mask=[".other"])
    page = _Page()
    _fail(module, {"editor_page": page})
    assert page.calls[0]["mask"] == [".pii"]


def test_a_failure_capture_with_no_mask_at_all_is_what_it_was(conftest):
    module = conftest()
    page = _Page()
    _fail(module, {"editor_page": page})
    assert page.calls == [{"mask": [], "full_page": True, "timeout": 5000}]
    assert _failure_files(module)


def test_a_masked_capture_that_fails_leaves_no_image_and_says_why(conftest, capsys):
    module = conftest(mask=[".pii"])
    page = _Page(error=RuntimeError('Page.screenshot: Unexpected token "" while parsing css selector\nCall log:'))
    _fail(module, {"editor_page": page}, scenario_mask=["[data-testid="])
    assert len(page.calls) == 1  # never retried without the masks
    assert _failure_files(module) == []
    out = capsys.readouterr().out
    assert 'not saved, the masked capture failed: Page.screenshot: Unexpected token ""' in out
    assert "Call log" not in out


def test_a_partial_file_is_removed(conftest):
    module = conftest(mask=[".pii"])
    _fail(module, {"editor_page": _Page(error=RuntimeError("crash"), partial=True)})
    assert _failure_files(module) == []


def test_an_earlier_runs_image_never_stands_in_for_this_one(conftest):
    """It may have been taken before the masks existed."""
    module = conftest(mask=[".pii"])
    page = _Page()
    _fail(module, {"editor_page": page})
    (name,) = _failure_files(module)
    (module.SCREENSHOTS_DIR / "_failures" / name).write_bytes(b"unmasked, from last week")
    _fail(module, {"editor_page": _Page(error=RuntimeError("crash"))})
    assert _failure_files(module) == []


def test_a_mask_that_cannot_even_be_built_fails_closed(conftest, capsys):
    module = conftest(mask=[{"a": "b"}])
    page = _Page()
    _fail(module, {"editor_page": page})
    assert page.calls == [] and _failure_files(module) == []
    assert "screenshot: not saved" in capsys.readouterr().out


def test_one_page_failing_closed_does_not_cost_the_other_its_capture(conftest):
    module = conftest(mask=[".pii"])
    _fail(module, {"editor_page": _Page(error=RuntimeError("crash")), "reviewer_page": _Page()})
    assert [n.rsplit("__", 1)[1] for n in _failure_files(module)] == ["reviewer_page.png"]


# ── Traces and videos ────────────────────────────────────────────────────────

_WARNING = "A screenshot mask is declared, but Playwright cannot mask a "


def _run(project, *args, command="run"):
    tests = project / "tests" / "e2e"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "conftest.py").write_text("", encoding="utf-8")
    (tests / "test_demo.py").write_text("", encoding="utf-8")
    (tests / "test_other.py").write_text("", encoding="utf-8")
    with patch("steppy.cli.subprocess.run", return_value=MagicMock(returncode=0)):
        return _invoke(command, *args)[1]


@pytest.mark.parametrize(
    "flags, said",
    [
        (["--trace"], "a trace: it records"),
        (["--video"], "a video: it records"),
        (["--trace", "--video"], "a trace or a video: they record"),
    ],
)
def test_run_warns_when_a_recording_would_show_what_is_masked(project, flags, said):
    _scenario(project / "scenarios", front="mask: .a")
    output = _run(project, *flags)
    assert output.count(_WARNING) == 1 and said in output


@pytest.mark.parametrize("where", ["config", "front", "step"])
def test_the_warning_covers_all_three_places_a_mask_is_declared(project, where):
    if where == "config":
        (project / "config.yaml").write_text(_CONFIG + "screenshot:\n  mask: ['.pii']\n", encoding="utf-8")
    front = "mask: .a" if where == "front" else ""
    step = "- mask: .a" if where == "step" else ""
    _scenario(project / "scenarios", front=front, step=step)
    assert _WARNING in _run(project, "--trace")


def test_the_warning_reads_the_configured_modes_too(project):
    (project / "config.yaml").write_text(_CONFIG + "report:\n  video: on-failure\n", encoding="utf-8")
    _scenario(project / "scenarios", front="mask: .a")
    assert _WARNING + "video" in _run(project)


def test_no_warning_without_a_recording_or_without_a_mask(project):
    _scenario(project / "scenarios", front="mask: .a")
    assert _WARNING not in _run(project)
    _scenario(project / "scenarios")
    assert _WARNING not in _run(project, "--trace", "--video")


def test_the_warning_is_about_the_scenarios_this_run_selects(project):
    _scenario(project / "scenarios", front="mask: .a", name="other.md")
    _scenario(project / "scenarios")
    assert _WARNING not in _run(project, "--trace", "--scenario", "demo")
    assert _WARNING in _run(project, "--trace", "--scenario", "other")


def test_a_scenario_that_cannot_be_read_does_not_stop_the_run(project):
    (project / "scenarios" / "broken.md").write_text("---\ntitle: T\nmask: []\n---\n", encoding="utf-8")
    _scenario(project / "scenarios", front="mask: .a")
    assert _WARNING in _run(project, "--trace")


def test_the_parallel_path_warns_too(project):
    _scenario(project / "scenarios", step="- mask: .a")
    with patch("steppy.coordinator.run_parallel", return_value=0) as parallel:
        output = _run(project, "--trace", "--workers", "2")
    assert parallel.call_args.kwargs["trace_mode"] == "on-failure"  # nothing is turned off
    assert output.count(_WARNING) == 1


def test_rerun_warns_too(project):
    _scenario(project / "scenarios", front="mask: .a")
    cache = project / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text('{"tests/e2e/test_demo.py::test_demo": true}', encoding="utf-8")
    assert _WARNING in _run(project, "--video", command="rerun")


# ── `steppy record` ──────────────────────────────────────────────────────────


def _record(*body: str, **kwargs):
    lines = "".join(f"    {line}\n" for line in body)
    source = "from playwright.sync_api import Page, expect\n\n\ndef test_example(page: Page) -> None:\n" + lines
    return codegen_source_to_scenario(source, role="admin", title="T", **kwargs)


def test_record_writes_the_masks_it_finds_into_the_draft(tmp_path):
    result = _record(
        'page.goto("https://a.co/settings")',
        'page.get_by_test_id("api-token").fill("tok_live_123")',
        'page.get_by_label("Password").fill("s3cr3t-pw")',
    )
    assert '[data-testid="api-token"]' in result.mask_selectors and len(result.mask_selectors) > 1
    path = tmp_path / "draft.md"
    path.write_text(result.markdown, encoding="utf-8")
    assert parse_scenario_file(path).mask == result.mask_selectors


def test_record_without_a_secret_writes_no_mask():
    assert "mask" not in _record('page.get_by_test_id("full-name").fill("Alice")').markdown


def test_record_no_longer_prints_a_block_to_paste(tmp_path, capsys):
    from steppy.record_cli import _print_report

    _print_report(_record('page.get_by_test_id("api-token").fill("tok")'), tmp_path / "draft.md")
    out = " ".join(capsys.readouterr().out.split())
    assert "Masked in this scenario's screenshots" in out and '[data-testid="api-token"]' in out
    assert "add to config.yaml" not in out


# ── `steppy migrate custom` ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "written, mask",
    [
        (f"- mask: {IBAN}", [IBAN]),
        ("- mask:\n    - '.a'\n    - .b", [".a", ".b"]),
        ("- mask: ['.a', '.b']", [".a", ".b"]),
        ("- mask: 'text=\"Jane Doe\"'", ['text="Jane Doe"']),
    ],
)
def test_migrating_a_masked_block_masks_every_step_it_becomes(tmp_path, written, mask):
    path = tmp_path / "demo.md"
    path.write_text(
        "---\ntitle: T\nauth: false\n---\n\n### 1. Two clicks\n- role: editor\n- action: custom\n"
        f"- note: kept once\n{written}\n- code: \"page.click('#a'); page.click('#b')\"\n",
        encoding="utf-8",
    )
    (outcome,) = apply_plan(plan_file(path), _generator(), write=True)
    assert outcome.applied, outcome.verification
    steps = parse_scenario_file(path).steps
    assert [s.action for s in steps] == ["click", "click"]
    assert [s.mask for s in steps] == [mask, mask]
    assert [s.note for s in steps] == ["kept once", None]
