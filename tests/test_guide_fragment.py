"""Unit tests for the guide.fragment.html.j2 template."""

import jinja2
import pytest


@pytest.fixture
def fragment_env():
    """Jinja2 environment loaded with the bundled templates."""
    import importlib.resources
    from pathlib import Path

    from steppy.guide import register_jinja_filters

    bundled = Path(importlib.resources.files("steppy") / "templates")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(bundled)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    register_jinja_filters(env)
    return env


def _render(env, **kwargs):
    tpl = env.get_template("guide.fragment.html.j2")
    defaults = {
        "title": "Test Guide",
        "role": "Editor",
        "steps": [],
    }
    defaults.update(kwargs)
    return tpl.render(**defaults)


class TestFragmentStructure:
    def test_no_doctype(self, fragment_env):
        html = _render(fragment_env)
        assert "<!DOCTYPE" not in html
        assert "<html" not in html
        assert "</html>" not in html

    def test_no_head_body_tags(self, fragment_env):
        html = _render(fragment_env)
        assert "<head" not in html
        assert "<body" not in html

    def test_no_script_tags(self, fragment_env):
        html = _render(fragment_env)
        assert "<script" not in html

    def test_has_style_block(self, fragment_env):
        html = _render(fragment_env)
        assert "<style>" in html
        assert ".steppy-guide" in html

    def test_scoped_css_classes(self, fragment_env):
        html = _render(fragment_env)
        assert ".steppy-guide .steppy-step" in html
        assert ".steppy-guide .steppy-stage" in html


class TestFragmentContent:
    def test_renders_title(self, fragment_env):
        html = _render(fragment_env, title="My Guide Title")
        assert "My Guide Title" in html

    def test_renders_role_badge(self, fragment_env):
        html = _render(fragment_env, role="Editor")
        assert "Editor" in html
        assert "steppy-role-badge" in html

    def test_renders_steps(self, fragment_env):
        steps = [
            {
                "index": 1,
                "title": "Navigate to dashboard",
                "subtitle": "Open the main page",
                "text": "Click the dashboard link.",
                "screenshot": "/guides/test/step-01.png",
                "viewport": None,
                "cursor": None,
                "frames": [{"screenshot": "/guides/test/step-01.png", "viewport": None, "cursor": None}],
            },
            {
                "index": 2,
                "title": "Click submit",
                "subtitle": None,
                "text": None,
                "screenshot": "/guides/test/step-02.png",
                "viewport": None,
                "cursor": None,
                "frames": [{"screenshot": "/guides/test/step-02.png", "viewport": None, "cursor": None}],
            },
        ]
        html = _render(fragment_env, steps=steps)
        assert "Navigate to dashboard" in html
        assert "Open the main page" in html
        assert "Click the dashboard link." in html
        assert "Click submit" in html
        assert 'src="/guides/test/step-01.png"' in html
        assert 'src="/guides/test/step-02.png"' in html

    def test_screenshot_src_uses_configured_path(self, fragment_env):
        steps = [
            {
                "index": 1,
                "title": "Step 1",
                "subtitle": None,
                "text": None,
                "screenshot": "/custom-assets/onboarding/step-01.png",
                "viewport": None,
                "cursor": None,
                "frames": [{"screenshot": "/custom-assets/onboarding/step-01.png", "viewport": None, "cursor": None}],
            },
        ]
        html = _render(fragment_env, steps=steps)
        assert 'src="/custom-assets/onboarding/step-01.png"' in html

    def test_multi_frame_renders_all_frames(self, fragment_env):
        steps = [
            {
                "index": 1,
                "title": "Fill form",
                "subtitle": None,
                "text": None,
                "screenshot": "/guides/test/step-01-a.png",
                "viewport": None,
                "cursor": None,
                "frames": [
                    {"screenshot": "/guides/test/step-01-a.png", "viewport": None, "cursor": None},
                    {"screenshot": "/guides/test/step-01-b.png", "viewport": None, "cursor": None},
                ],
            },
        ]
        html = _render(fragment_env, steps=steps)
        assert 'src="/guides/test/step-01-a.png"' in html
        assert 'src="/guides/test/step-01-b.png"' in html

    def test_renders_markdown_bold_in_text(self, fragment_env):
        steps = [
            {
                "index": 1,
                "title": "Plain title",
                "subtitle": None,
                "text": "Click the **Create** button to open the form.",
                "screenshot": "/s.png",
                "viewport": None,
                "cursor": None,
                "frames": [{"screenshot": "/s.png", "viewport": None, "cursor": None}],
            },
        ]
        html = _render(fragment_env, steps=steps)
        assert "<strong>Create</strong>" in html
        assert "**Create**" not in html

    def test_omits_subtitle_when_none(self, fragment_env):
        steps = [
            {
                "index": 1,
                "title": "Step",
                "subtitle": None,
                "text": None,
                "screenshot": "/s.png",
                "viewport": None,
                "cursor": None,
                "frames": [{"screenshot": "/s.png", "viewport": None, "cursor": None}],
            },
        ]
        html = _render(fragment_env, steps=steps)
        # The CSS defines the class, but no element should use it
        body_html = html.split("</style>")[-1]
        assert "steppy-step-subtitle" not in body_html
