"""Unit tests for the parser module."""

import pytest
from pathlib import Path
from textwrap import dedent

from steppy.parser import (
    parse_scenario_file,
    parse_scenarios_from_directory,
    _parse_step_content,
    validate_scenario_roles,
    validate_scenario_layouts,
    ParseError,
)
from steppy.models import Scenario, Step


@pytest.fixture
def temp_scenario_file(tmp_path):
    """Create a temporary scenario file for testing."""

    def _create_file(content: str) -> Path:
        file_path = tmp_path / "test_scenario.md"
        file_path.write_text(dedent(content), encoding="utf-8")
        return file_path

    return _create_file


def test_parse_simple_scenario(temp_scenario_file):
    """Test parsing a simple scenario with basic metadata and steps."""
    content = """
    ---
    title: Test Scenario
    description: A test scenario
    tags:
      - e2e
      - test
    priority: high
    ---

    ### 1. Navigate to homepage
    - role: editor
    - action: navigate
    - target: /home

    ### 2. Click button
    - role: editor
    - action: click
    - selector: button#submit
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert scenario.title == "Test Scenario"
    assert scenario.description == "A test scenario"
    assert scenario.tags == ["e2e", "test"]
    assert scenario.priority == "high"
    assert len(scenario.steps) == 2
    assert scenario.slug == "test_scenario"


def test_parse_step_with_guide_text(temp_scenario_file):
    """Test parsing a step with guide text in multiple locales."""
    content = """
    ---
    title: Guide Test
    ---

    ### 1. Test step
    - role: editor
    - action: click
    - selector: button
    - guide-text:
      fr: "Cliquez sur le bouton"
      en: "Click the button"
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert len(scenario.steps) == 1
    step = scenario.steps[0]
    assert step.guide_text is not None
    # Guide text is now a structured GuideTextContent object
    assert step.guide_text.fr is not None
    assert step.guide_text.fr.title == "Cliquez sur le bouton"
    assert step.guide_text.fr.text == "Cliquez sur le bouton"
    assert step.guide_text.en is not None
    assert step.guide_text.en.title == "Click the button"
    assert step.guide_text.en.text == "Click the button"


def test_parse_step_with_wait_action(temp_scenario_file):
    """Test parsing a wait action step."""
    content = """
    ---
    title: Wait Test
    ---

    ### 1. Wait for processing
    - action: wait
    - duration: 5s
    - note: "Waiting for backend processing"
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert len(scenario.steps) == 1
    step = scenario.steps[0]
    assert step.action == "wait"
    assert step.duration == "5s"
    assert step.note == "Waiting for backend processing"
    assert step.role is None  # wait doesn't require a role


def test_parse_step_with_expect(temp_scenario_file):
    """Test parsing a step with an assertion."""
    content = """
    ---
    title: Assertion Test
    ---

    ### 1. Submit form
    - role: editor
    - action: click
    - selector: button[type="submit"]
    - expect: toast("Success")
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert len(scenario.steps) == 1
    step = scenario.steps[0]
    assert step.expect == 'toast("Success")'


def test_parse_step_with_screenshot_settings(temp_scenario_file):
    """Test parsing step screenshot settings."""
    content = """
    ---
    title: Screenshot Test
    ---

    ### 1. With screenshot
    - role: editor
    - action: click
    - selector: button
    - screenshot: editor

    ### 2. Without screenshot
    - role: editor
    - action: click
    - selector: button
    - screenshot: false
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert len(scenario.steps) == 2
    assert scenario.steps[0].screenshot == "editor"
    assert scenario.steps[1].screenshot is False  # Parsed as boolean


def test_parse_fill_form_action(temp_scenario_file):
    """Test parsing a fill-form action with multiple fields."""
    content = """
    ---
    title: Form Test
    ---

    ### 1. Fill login form
    - role: editor
    - action: fill-form
    - fields:
      email: "test@example.com"
      password: "secret123"
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert len(scenario.steps) == 1
    step = scenario.steps[0]
    assert step.action == "fill-form"
    assert step.fields == {"email": "test@example.com", "password": "secret123"}
    # Default: progressive capture is off unless explicitly opted in.
    assert step.interactive_progressive is False


def test_parse_fill_form_with_interactive_progressive(temp_scenario_file):
    """``interactive-progressive: true`` opts a fill-form step into per-field capture.

    The kebab key must alias to the snake_case attribute, and field order
    (which drives the per-frame mapping) must be preserved.
    """
    content = """
    ---
    title: Progressive Form Test
    ---

    ### 1. Fill login form progressively
    - role: editor
    - action: fill-form
    - interactive-progressive: true
    - fields:
      email: "test@example.com"
      password: "secret123"
      remember: "on"
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    step = scenario.steps[0]
    assert step.action == "fill-form"
    assert step.interactive_progressive is True
    # Source order matters — the per-field capture frame indices are derived
    # from this order, so dict iteration must match the YAML.
    assert list(step.fields.keys()) == ["email", "password", "remember"]


def test_interactive_progressive_rejected_on_non_fill_form(temp_scenario_file):
    """``interactive-progressive: true`` must error on non-fill-form actions."""
    content = """
    ---
    title: Bad Progressive Test
    ---

    ### 1. Click with progressive flag
    - role: editor
    - action: click
    - selector: button
    - interactive-progressive: true
    """

    file_path = temp_scenario_file(content)
    with pytest.raises(Exception) as exc_info:
        parse_scenario_file(file_path)
    assert "interactive-progressive" in str(exc_info.value)
    assert "fill-form" in str(exc_info.value)


def test_get_roles_from_scenario(temp_scenario_file):
    """Test extracting unique roles from a scenario."""
    content = """
    ---
    title: Multi-role Test
    ---

    ### 1. Editor action
    - role: editor
    - action: click
    - selector: button

    ### 2. Reviewer action
    - role: reviewer
    - action: click
    - selector: button

    ### 3. Another editor action
    - role: editor
    - action: navigate
    - target: /home
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    roles = scenario.get_roles()
    assert roles == {"editor", "reviewer"}


def test_has_tag_method(temp_scenario_file):
    """Test the has_tag method."""
    content = """
    ---
    title: Tag Test
    tags:
      - e2e
      - critical
    ---

    ### 1. Test step
    - role: editor
    - action: click
    - selector: button
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert scenario.has_tag("e2e") is True
    assert scenario.has_tag("critical") is True
    assert scenario.has_tag("regression") is False


def test_missing_title_raises_error(temp_scenario_file):
    """Test that missing title in frontmatter raises ParseError."""
    content = """
    ---
    description: Missing title
    ---

    ### 1. Test step
    - role: editor
    - action: click
    - selector: button
    """

    file_path = temp_scenario_file(content)

    with pytest.raises(ParseError, match="missing required 'title'"):
        parse_scenario_file(file_path)


def test_invalid_action_type_raises_error(temp_scenario_file):
    """Test that an invalid action type raises a validation error."""
    content = """
    ---
    title: Invalid Action
    ---

    ### 1. Bad step
    - role: editor
    - action: invalid_action
    - selector: button
    """

    file_path = temp_scenario_file(content)

    with pytest.raises(ParseError, match="Unknown action type"):
        parse_scenario_file(file_path)


def test_parse_scenario_with_timeout(temp_scenario_file):
    """Test parsing scenario with custom timeout."""
    content = """
    ---
    title: Timeout Test
    timeout: 60000
    ---

    ### 1. Test step
    - role: editor
    - action: click
    - selector: button
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert scenario.timeout == 60000


def test_parse_scenario_with_seed(temp_scenario_file):
    """Test parsing scenario with seed script."""
    content = """
    ---
    title: Seed Test
    seed: base
    ---

    ### 1. Test step
    - role: editor
    - action: click
    - selector: button
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert scenario.seed.script == "base"
    assert scenario.seed.run == "before"


def test_step_numbering(temp_scenario_file):
    """Test that steps are numbered correctly."""
    content = """
    ---
    title: Step Numbering
    ---

    ### 1. First step
    - role: editor
    - action: click
    - selector: button

    ### 2. Second step
    - role: editor
    - action: navigate
    - target: /home

    ### 3. Third step
    - role: editor
    - action: fill
    - selector: input
    - value: "test"
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert len(scenario.steps) == 3
    assert scenario.steps[0].step_number == 1
    assert scenario.steps[1].step_number == 2
    assert scenario.steps[2].step_number == 3


def test_parse_step_content():
    """Test the _parse_step_content helper function."""
    content = """
- role: editor
- action: click
- selector: button#submit
- screenshot: true
    """

    result = _parse_step_content(content, "Test Step", Path("test.md"))

    assert result["role"] == "editor"
    assert result["action"] == "click"
    assert result["selector"] == "button#submit"
    assert result["screenshot"] == "true"


def test_parse_empty_scenario_body(temp_scenario_file):
    """Test parsing a scenario with no steps."""
    content = """
    ---
    title: Empty Scenario
    ---
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert scenario.title == "Empty Scenario"
    assert len(scenario.steps) == 0


def test_parse_scenario_with_seed_timing(temp_scenario_file):
    """Test parsing scenario with seed timing configuration."""
    content = """
    ---
    title: Seed Timing Test
    seed:
      script: base
      run: both
    ---

    ### 1. Test step
    - role: editor
    - action: click
    - selector: button
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert scenario.seed.script == "base"
    assert scenario.seed.run == "both"


def test_parse_scenario_with_seed_after(temp_scenario_file):
    """Test parsing scenario with seed run after."""
    content = """
    ---
    title: Seed After Test
    seed:
      script: base
      run: after
    ---

    ### 1. Test step
    - role: editor
    - action: click
    - selector: button
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert scenario.seed.script == "base"
    assert scenario.seed.run == "after"


def test_parse_scenario_with_invalid_seed_run(temp_scenario_file):
    """Test that invalid seed run value raises error."""
    content = """
    ---
    title: Invalid Seed
    seed:
      script: base
      run: during
    ---

    ### 1. Test step
    - role: editor
    - action: click
    - selector: button
    """

    file_path = temp_scenario_file(content)

    with pytest.raises(ParseError):
        parse_scenario_file(file_path)


def test_file_not_found_raises_error():
    """Test that parsing a non-existent file raises ParseError."""
    non_existent_path = Path("non_existent_scenario.md")

    with pytest.raises(ParseError, match="not found"):
        parse_scenario_file(non_existent_path)


def test_has_all_tags_method(temp_scenario_file):
    """Test the has_all_tags method with AND logic."""
    content = """
    ---
    title: Multi Tag Test
    tags:
      - e2e
      - critical
      - customer
    ---

    ### 1. Test step
    - role: editor
    - action: click
    - selector: button
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert scenario.has_all_tags(["e2e"]) is True
    assert scenario.has_all_tags(["e2e", "critical"]) is True
    assert scenario.has_all_tags(["e2e", "critical", "customer"]) is True
    assert scenario.has_all_tags(["e2e", "regression"]) is False
    assert scenario.has_all_tags([]) is True


def test_parse_capture_step_redis(temp_scenario_file):
    """Capture action with a redis fetcher and retry/interval."""
    content = """
    ---
    title: Capture Test
    ---

    ### 1. Capture OTP from Redis
    - action: capture
    - store: otp_code
    - from:
      type: redis
      key: "steppy:otp:user-123"
    - retry: 5
    - interval: 500ms
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    assert len(scenario.steps) == 1
    step = scenario.steps[0]
    assert step.action == "capture"
    assert step.store == "otp_code"
    assert step.from_ == {"type": "redis", "key": "steppy:otp:user-123"}
    assert step.retry == 5
    assert step.interval == "500ms"


def test_parse_capture_step_shell(temp_scenario_file):
    """Capture action with a shell fetcher (no redis required)."""
    content = """
    ---
    title: Capture Shell
    ---

    ### 1. Capture echoed value
    - action: capture
    - store: greeting
    - from:
      type: shell
      command: "echo hello"
    """

    file_path = temp_scenario_file(content)
    scenario = parse_scenario_file(file_path)

    step = scenario.steps[0]
    assert step.action == "capture"
    assert step.store == "greeting"
    assert step.from_["type"] == "shell"
    assert step.from_["command"] == "echo hello"


def test_capture_without_store_raises(temp_scenario_file):
    """capture action requires 'store'."""
    content = """
    ---
    title: Bad Capture
    ---

    ### 1. Missing store
    - action: capture
    - from:
      type: shell
      command: "echo x"
    """

    file_path = temp_scenario_file(content)
    with pytest.raises(ParseError, match="store"):
        parse_scenario_file(file_path)


def test_capture_without_from_raises(temp_scenario_file):
    """capture action requires 'from'."""
    content = """
    ---
    title: Bad Capture
    ---

    ### 1. Missing from
    - action: capture
    - store: x
    """

    file_path = temp_scenario_file(content)
    with pytest.raises(ParseError, match="from"):
        parse_scenario_file(file_path)


def test_capture_with_unknown_type_raises(temp_scenario_file):
    """from.type must be one of redis/http/shell."""
    content = """
    ---
    title: Bad Capture Type
    ---

    ### 1. Unknown type
    - action: capture
    - store: x
    - from:
      type: ftp
      key: foo
    """

    file_path = temp_scenario_file(content)
    with pytest.raises(ParseError, match="from.type"):
        parse_scenario_file(file_path)


def test_parse_scenarios_from_directory_multi_tag_filter(tmp_path):
    """Test filtering scenarios by multiple tags (AND logic)."""
    (tmp_path / "scenario_a.md").write_text(
        dedent("""\
    ---
    title: Scenario A
    tags:
      - e2e
      - customer
    ---

    ### 1. Step
    - role: editor
    - action: click
    - selector: button
    """),
        encoding="utf-8",
    )

    (tmp_path / "scenario_b.md").write_text(
        dedent("""\
    ---
    title: Scenario B
    tags:
      - e2e
      - reviewer
    ---

    ### 1. Step
    - role: editor
    - action: click
    - selector: button
    """),
        encoding="utf-8",
    )

    # Both have e2e
    result = parse_scenarios_from_directory(tmp_path, tag_filter=["e2e"])
    assert len(result) == 2

    # Only A has both e2e and customer
    result = parse_scenarios_from_directory(tmp_path, tag_filter=["e2e", "customer"])
    assert len(result) == 1
    assert result[0].title == "Scenario A"

    # No filter returns all
    result = parse_scenarios_from_directory(tmp_path, tag_filter=None)
    assert len(result) == 2


# ── Guide cursor feature tests ──────────────────────────────────────────────


def test_parse_scenario_with_guide_cursor_frontmatter(temp_scenario_file):
    """The optional `guide.cursor` frontmatter block parses and overrides defaults."""
    content = """
    ---
    title: Guided tour
    guide:
      cursor:
        pre_action_ms: 400
        action_ms: 600
        post_action_ms: 250
        travel_speed_px_per_ms: 2.5
    ---

    ### 1. Go home
    - role: editor
    - action: navigate
    - target: /home
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.guide.cursor.pre_action_ms == 400
    assert scenario.guide.cursor.action_ms == 600
    assert scenario.guide.cursor.post_action_ms == 250
    assert scenario.guide.cursor.travel_speed_px_per_ms == 2.5


def test_parse_scenario_without_guide_block_uses_defaults(temp_scenario_file):
    """Scenarios that omit the `guide` block get default cursor timings."""
    content = """
    ---
    title: Plain scenario
    ---

    ### 1. Go home
    - role: editor
    - action: navigate
    - target: /home
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.guide.cursor.pre_action_ms == 200
    assert scenario.guide.cursor.action_ms == 300
    assert scenario.guide.cursor.post_action_ms == 150
    assert scenario.guide.cursor.travel_speed_px_per_ms == 1.5


def test_parse_step_with_guide_cursor_override(temp_scenario_file):
    """Custom steps can carry a `guide-cursor` override parsed into GuideCursorOverride."""
    content = """
    ---
    title: Custom with cursor
    ---

    ### 1. Fill DOB manually
    - role: editor
    - action: custom
    - code: "page.fill('x', 'y')"
    - guide-cursor:
        behavior: type
        target: '[data-testid="dob"] input'
        value: "21-06-1985"
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    step = scenario.steps[0]
    assert step.guide_cursor is not None
    assert step.guide_cursor.behavior == "type"
    assert step.guide_cursor.target == '[data-testid="dob"] input'
    assert step.guide_cursor.value == "21-06-1985"


def test_parse_step_without_guide_cursor_is_none(temp_scenario_file):
    """Steps without the override have guide_cursor == None."""
    content = """
    ---
    title: Plain
    ---

    ### 1. Click
    - role: editor
    - action: click
    - selector: button#submit
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].guide_cursor is None


def test_unknown_frontmatter_still_rejected(temp_scenario_file):
    """extra=forbid regression — typos in frontmatter still raise ParseError."""
    content = """
    ---
    title: Typo scenario
    guiide:
      cursor:
        pre_action_ms: 1
    ---

    ### 1. Go
    - role: editor
    - action: navigate
    - target: /home
    """
    with pytest.raises(ParseError):
        parse_scenario_file(temp_scenario_file(content))


def test_invalid_guide_cursor_behavior_rejected(temp_scenario_file):
    """Unknown `behavior` on guide-cursor is rejected by the Literal validator."""
    content = """
    ---
    title: Bad behavior
    ---

    ### 1. Custom
    - role: editor
    - action: custom
    - code: "pass"
    - guide-cursor:
        behavior: bogus
        target: 'x'
    """
    with pytest.raises(ParseError):
        parse_scenario_file(temp_scenario_file(content))


def test_guide_enabled_defaults_to_false(temp_scenario_file):
    """Scenarios without a `guide` block default to guide.enabled=False."""
    content = """
    ---
    title: No guide block
    ---

    ### 1. Navigate
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.guide.enabled is False


def test_guide_enabled_true(temp_scenario_file):
    """`guide.enabled: true` in frontmatter is parsed onto GuideConfig."""
    content = """
    ---
    title: Guide scenario
    guide:
      enabled: true
    ---

    ### 1. Navigate
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.guide.enabled is True


def test_guide_enabled_with_cursor(temp_scenario_file):
    """`guide.enabled` coexists with `guide.cursor` timings."""
    content = """
    ---
    title: Guide with cursor
    guide:
      enabled: true
      cursor:
        pre_action_ms: 100
    ---

    ### 1. Navigate
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.guide.enabled is True
    assert scenario.guide.cursor.pre_action_ms == 100


# ── Regression: YAML block scalar values for `code` and similar ──────────────


def test_custom_step_with_block_scalar_code_preserves_newlines(temp_scenario_file):
    """Regression: `- code: |` followed by indented lines must be parsed as a
    multiline string. Before the fix, the parser stored the literal `"|"` as
    the value and silently dropped the indented body, producing generated test
    files with a stray `|` character that fail to import with SyntaxError."""
    content = """
    ---
    title: Custom step test
    auth: false
    ---

    ### 1. Custom interaction
    - role: editor
    - action: custom
    - code: |
        page.click('[data-testid="foo"]')
        page.fill('[data-testid="bar"]', 'baz')
        page.keyboard.press('Enter')
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    step = scenario.steps[0]
    assert step.action == "custom"
    # The block scalar must be a real multiline string, NOT the literal "|"
    assert step.code != "|"
    assert step.code is not None
    assert "page.click" in step.code
    assert "page.fill" in step.code
    assert "page.keyboard.press" in step.code
    # `|` literal scalar preserves newlines between lines
    assert step.code.count("\n") >= 2


def test_block_scalar_strip_chomping_indicator(temp_scenario_file):
    """`|-` strips the trailing newline (vs `|` which keeps one)."""
    content = """
    ---
    title: Strip chomping
    auth: false
    ---

    ### 1. Custom
    - role: editor
    - action: custom
    - code: |-
        x = 1
        y = 2
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].code == "x = 1\ny = 2"


def test_block_scalar_keep_chomping_indicator(temp_scenario_file):
    """`|+` keeps all trailing newlines."""
    content = """
    ---
    title: Keep chomping
    auth: false
    ---

    ### 1. Custom
    - role: editor
    - action: custom
    - code: |+
        x = 1
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].code is not None
    assert scenario.steps[0].code.startswith("x = 1")


def test_folded_block_scalar(temp_scenario_file):
    """`>` folds newlines into spaces."""
    content = """
    ---
    title: Folded scalar
    auth: false
    ---

    ### 1. Custom
    - role: editor
    - action: custom
    - code: >
        page.click('foo')
        page.fill('bar', 'baz')
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    code = scenario.steps[0].code
    # `>` folds line breaks to single spaces
    assert code is not None
    assert "page.click('foo') page.fill('bar', 'baz')" in code


def test_block_scalar_followed_by_more_step_fields(temp_scenario_file):
    """Fields appearing after a block scalar must still be parsed normally —
    the lookahead must stop at the next `- key:` line."""
    content = """
    ---
    title: Block scalar then more fields
    auth: false
    ---

    ### 1. Custom
    - role: editor
    - action: custom
    - code: |
        x = 1
        y = 2
    - screenshot: editor
    - guide-text:
        fr: "Action personnalisée."
        en: "Custom action."
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    step = scenario.steps[0]
    assert step.code == "x = 1\ny = 2"
    assert step.screenshot == "editor"
    assert step.guide_text is not None
    assert step.guide_text.fr is not None


def test_generated_test_file_with_custom_block_scalar_is_valid_python(temp_scenario_file):
    """End-to-end regression: a scenario with `code: |` must produce a generated
    test file that imports without SyntaxError. This was the user-visible
    failure mode (`SyntaxError` on a stray `|` at the line where the code
    block should have been emitted)."""
    import ast
    from steppy.config import load_config
    from steppy.generator import SteppyGenerator

    content = """
    ---
    title: Custom step e2e
    auth: false
    ---

    ### 1. Open
    - role: editor
    - action: navigate
    - target: /home

    ### 2. Custom
    - role: editor
    - action: custom
    - code: |
        page.click('[data-testid="foo"]')
        page.fill('[data-testid="bar"]', 'hello')
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    py_source = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(py_source)  # raises SyntaxError if the `|` bug regresses


def test_generated_docstring_path_is_safe_on_windows_paths(temp_scenario_file, tmp_path):
    """Regression: the docstring at the top of generated test files embeds
    `scenario.file_path`. On Windows, paths like `C:\\Users\\...` contain `\\U`,
    which Python's tokenizer treats as a (truncated) unicode escape and rejects
    with `SyntaxError: (unicode error) ... truncated \\UXXXXXXXX escape`. The
    template normalises backslashes to forward slashes to avoid every flavour
    of this problem (\\U, \\u, \\x, \\N, …).

    We force a Windows-shaped path into the docstring by spoofing the parsed
    scenario's file_path, then assert ast.parse succeeds."""
    import ast
    from steppy.config import load_config
    from steppy.generator import SteppyGenerator

    content = """
    ---
    title: Path escape regression
    auth: false
    ---

    ### 1. Navigate
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    # Spoof a Windows-style absolute path containing every backslash escape
    # sequence that has ever broken Python's tokenizer in a docstring.
    scenario.file_path = Path(r"C:\Users\someone\AppData\Local\notes.md")

    py_source = SteppyGenerator(load_config()).generate_test_file(scenario)

    # The path must appear in the docstring with forward slashes (so the
    # tokenizer never sees \U, \u, \x, \N as escape starts).
    assert "C:/Users/someone/AppData/Local/notes.md" in py_source
    assert r"C:\Users" not in py_source

    # And the file must parse as Python.
    ast.parse(py_source)


# ── Configurable role tests ─────────────────────────────────────────────────


def test_screenshot_accepts_arbitrary_role(temp_scenario_file):
    """The model layer must accept any string as a screenshot role — config
    membership is checked post-parse, not by the validator."""
    content = """
    ---
    title: Custom role
    auth: false
    ---

    ### 1. Navigate
    - role: manager
    - action: navigate
    - target: /
    - screenshot: manager
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].role == "manager"
    assert scenario.steps[0].screenshot == "manager"


def test_screenshot_keeps_bool_and_all_sentinel(temp_scenario_file):
    """Existing string-bool and 'all' sentinel handling must still work."""
    content = """
    ---
    title: Sentinel forms
    auth: false
    ---

    ### 1. A
    - role: member
    - action: navigate
    - target: /
    - screenshot: all

    ### 2. B
    - role: member
    - action: navigate
    - target: /
    - screenshot: false

    ### 3. C
    - role: member
    - action: navigate
    - target: /
    - screenshot: true
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].screenshot == "all"
    assert scenario.steps[1].screenshot is False
    assert scenario.steps[2].screenshot is True


def test_validate_scenario_roles_accepts_configured_roles(temp_scenario_file):
    content = """
    ---
    title: OK
    auth: false
    ---

    ### 1. Step
    - role: manager
    - action: navigate
    - target: /
    - screenshot: member
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    errors = validate_scenario_roles(scenario, {"admin", "manager", "member"})
    assert errors == []


def test_validate_scenario_roles_reports_unknown_role(temp_scenario_file):
    content = """
    ---
    title: Unknown
    auth: false
    ---

    ### 1. Step
    - role: ghost
    - action: navigate
    - target: /
    - screenshot: phantom
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    errors = validate_scenario_roles(scenario, {"admin", "manager", "member"})
    assert len(errors) == 2
    assert any("'ghost'" in e for e in errors)
    assert any("'phantom'" in e for e in errors)
    for err in errors:
        assert "admin, manager, member" in err


# ── Conditional branching: only-in + layouts ───────────────────────────────


def test_step_only_in_test(temp_scenario_file):
    """`only-in: test` parses to Step.only_in == 'test'."""
    content = """
    ---
    title: Only-in test
    ---

    ### 1. Test-only assertion
    - role: editor
    - action: assert
    - expect: visible(button)
    - only-in: test
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].only_in == "test"


def test_step_only_in_guide(temp_scenario_file):
    """`only-in: guide` parses to Step.only_in == 'guide'."""
    content = """
    ---
    title: Only-in guide
    ---

    ### 1. Guide-only hover
    - role: editor
    - action: hover
    - selector: tooltip
    - only-in: guide
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].only_in == "guide"


def test_step_only_in_default_is_none(temp_scenario_file):
    """A step without `only-in` has only_in == None (runs in both modes)."""
    content = """
    ---
    title: Default
    ---

    ### 1. Plain step
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].only_in is None


def test_step_only_in_invalid_value_raises(temp_scenario_file):
    """`only-in: bogus` is rejected by the Literal validator."""
    content = """
    ---
    title: Bad only-in
    ---

    ### 1. Step
    - role: editor
    - action: navigate
    - target: /
    - only-in: bogus
    """
    with pytest.raises(ParseError):
        parse_scenario_file(temp_scenario_file(content))


def test_step_layouts_list(temp_scenario_file):
    """`layouts: [desktop, mobile]` parses to Step.layouts."""
    content = """
    ---
    title: Layouts
    ---

    ### 1. Layout-restricted step
    - role: editor
    - action: click
    - selector: button
    - layouts:
      - desktop
      - mobile
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].layouts == ["desktop", "mobile"]


def test_step_layouts_default_is_none(temp_scenario_file):
    """A step without `layouts` has layouts == None (runs in every layout)."""
    content = """
    ---
    title: No layouts
    ---

    ### 1. Plain
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].layouts is None


def test_step_only_in_test_with_layouts_accepted(temp_scenario_file):
    """`only-in: test` plus `layouts: [...]` is a valid combination: run the
    step only outside guide mode, only when the listed layout is parametrized.
    Useful when a step is mobile-only AND should not appear in guide screenshots."""
    content = """
    ---
    title: Test-only mobile step
    ---

    ### 1. Mobile-only test step
    - role: editor
    - action: click
    - selector: button
    - only-in: test
    - layouts:
      - mobile
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.steps[0].only_in == "test"
    assert scenario.steps[0].layouts == ["mobile"]


def test_step_layouts_empty_list_rejected(temp_scenario_file):
    """An empty `layouts: []` is rejected (use omission to mean 'all')."""
    # Use frontmatter YAML for the empty list since the markdown-list parser
    # can't represent an empty list; the model validator still runs.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Step(action="click", role="editor", selector="button", layouts=[])


def test_step_layouts_blank_string_rejected():
    """Blank entries inside `layouts` are rejected."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Step(action="click", role="editor", selector="button", layouts=[""])


def test_validate_scenario_layouts_accepts_configured(temp_scenario_file):
    """`step.layouts` entries that match `config.screenshot.layouts.keys()` produce no errors."""
    content = """
    ---
    title: OK
    auth: false
    ---

    ### 1. Step
    - role: editor
    - action: click
    - selector: button
    - layouts:
      - mobile
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    errors = validate_scenario_layouts(scenario, {"desktop", "mobile"})
    assert errors == []


def test_validate_scenario_layouts_reports_unknown(temp_scenario_file):
    """Unknown layout names are reported with the available list."""
    content = """
    ---
    title: Unknown
    auth: false
    ---

    ### 1. Step
    - role: editor
    - action: click
    - selector: button
    - layouts:
      - tablet
      - mobile
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    errors = validate_scenario_layouts(scenario, {"desktop", "mobile"})
    assert len(errors) == 1
    assert "'tablet'" in errors[0]
    assert "desktop, mobile" in errors[0]


def test_validate_scenario_layouts_empty_config_reports(temp_scenario_file):
    """When no layouts are configured, any layout reference is reported."""
    content = """
    ---
    title: No config
    auth: false
    ---

    ### 1. Step
    - role: editor
    - action: click
    - selector: button
    - layouts:
      - mobile
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    errors = validate_scenario_layouts(scenario, set())
    assert len(errors) == 1
    assert "<none configured>" in errors[0]


def test_validate_scenario_layouts_no_layouts_field(temp_scenario_file):
    """Steps without `layouts` produce no errors regardless of config."""
    content = """
    ---
    title: No layouts on steps
    auth: false
    ---

    ### 1. Step
    - role: editor
    - action: click
    - selector: button
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    errors = validate_scenario_layouts(scenario, set())
    assert errors == []


# ── Scenario-level `layouts:` declaration ───────────────────────────────────


def test_scenario_layouts_frontmatter_parses(temp_scenario_file):
    """`layouts: [desktop, mobile]` in frontmatter parses to Scenario.layouts."""
    content = """
    ---
    title: Responsive flow
    auth: false
    layouts:
      - desktop
      - mobile
    ---

    ### 1. Step
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.layouts == ["desktop", "mobile"]


def test_scenario_layouts_default_is_none(temp_scenario_file):
    """A scenario without `layouts` has layouts == None (no scenario-level dimension)."""
    content = """
    ---
    title: No layouts
    auth: false
    ---

    ### 1. Step
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert scenario.layouts is None


def test_scenario_layouts_empty_list_rejected():
    """An empty `layouts: []` on a scenario is rejected (omit to mean 'no dimension')."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Scenario(title="x", layouts=[])


def test_scenario_layouts_blank_string_rejected():
    """Blank entries inside a scenario `layouts` are rejected."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Scenario(title="x", layouts=[""])


def test_validate_scenario_layouts_reports_unknown_scenario_level(temp_scenario_file):
    """An unknown scenario-level layout is reported, prefixed with `scenario:`."""
    content = """
    ---
    title: Unknown scenario layout
    auth: false
    layouts:
      - tablet
    ---

    ### 1. Step
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    errors = validate_scenario_layouts(scenario, {"desktop", "mobile"})
    assert len(errors) == 1
    assert errors[0].startswith("scenario:")
    assert "'tablet'" in errors[0]
    assert "desktop, mobile" in errors[0]


def test_validate_scenario_layouts_accepts_configured_scenario_level(temp_scenario_file):
    """A scenario-level layout matching config produces no errors."""
    content = """
    ---
    title: OK
    auth: false
    layouts:
      - mobile
    ---

    ### 1. Step
    - role: editor
    - action: navigate
    - target: /
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert validate_scenario_layouts(scenario, {"desktop", "mobile"}) == []


def test_validate_scenario_layouts_scenario_and_step_both_reported(temp_scenario_file):
    """Scenario-level and step-level unknown layouts both surface."""
    content = """
    ---
    title: Both
    auth: false
    layouts:
      - tablet
    ---

    ### 1. Step
    - role: editor
    - action: click
    - selector: button
    - layouts:
      - phablet
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    errors = validate_scenario_layouts(scenario, {"desktop", "mobile"})
    assert len(errors) == 2
    assert any(e.startswith("scenario:") and "'tablet'" in e for e in errors)
    assert any("'phablet'" in e for e in errors)


def test_generator_wraps_filtered_step_in_step_runs_guard(temp_scenario_file):
    """Steps with `only-in` or `layouts` must be wrapped in `if _step_runs(...):`
    in the generated test file. Unfiltered steps must NOT be wrapped (so the
    common path stays byte-identical to pre-feature output)."""
    import ast
    from steppy.config import load_config
    from steppy.generator import SteppyGenerator

    content = """
    ---
    title: Guarded
    auth: false
    ---

    ### 1. Plain step
    - role: editor
    - action: navigate
    - target: /

    ### 2. Guide-only mobile step
    - role: editor
    - action: click
    - selector: '[data-testid="x"]'
    - only-in: guide
    - layouts:
      - mobile

    ### 3. Test-only assertion
    - role: editor
    - action: assert
    - expect: visible(.dashboard)
    - only-in: test
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    py_source = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(py_source)  # must be valid Python

    # Step 1 has no guard
    assert "Step 1: Plain step" in py_source
    step1_block = py_source[py_source.find("Step 1") : py_source.find("Step 2")]
    assert "_step_runs" not in step1_block

    # Step 2 has guard with both args
    step2_block = py_source[py_source.find("Step 2") : py_source.find("Step 3")]
    assert "_step_runs('guide', ['mobile'])" in step2_block

    # Step 3 has guard with only_in
    step3_block = py_source[py_source.find("Step 3") :]
    assert "_step_runs('test', None)" in step3_block


def test_screenshot_config_layouts_parses_from_yaml():
    """`screenshot.layouts` in config.yaml maps to dict[str, LayoutConfig]."""
    from steppy.models import SteppyConfig

    cfg = SteppyConfig(
        apps={
            "editor": {
                "name": "Editor",
                "url": "http://localhost",
                "auth": {"mode": "auth0", "credentials": {"username": "u", "password": "p"}},
            }
        },
        screenshot={
            "layouts": {
                "desktop": {"width": 1280, "height": 720},
                "mobile": {"width": 390, "height": 844},
            }
        },
    )
    assert "desktop" in cfg.screenshot.layouts
    assert cfg.screenshot.layouts["desktop"].width == 1280
    assert cfg.screenshot.layouts["mobile"].height == 844


def test_validate_scenario_roles_ignores_all_sentinel_and_booleans(temp_scenario_file):
    content = """
    ---
    title: Sentinels
    auth: false
    ---

    ### 1. Step
    - role: editor
    - action: navigate
    - target: /
    - screenshot: all

    ### 2. Step
    - role: editor
    - action: navigate
    - target: /
    - screenshot: true
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    errors = validate_scenario_roles(scenario, {"editor"})
    assert errors == []
