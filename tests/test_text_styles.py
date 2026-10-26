"""``textStyles``: styles for strong / em / code / links in step text.

The block is validated (its values land in a ``<style>`` block), emitted as
``--steppy-*`` custom properties plus the rules that read them, and absent
from every output when unset.
"""

import pytest
from pydantic import ValidationError

from steppy.build import BuildAssembler, text_styles_css
from steppy.build_models import TextStylesConfig
from tests.test_build import _setup_project

STYLES = {
    "strong": {"weight": 600, "color": "#9a3412"},
    "em": {"style": "normal"},
    "code": {"font": "'JetBrains Mono', monospace", "background": "rgb(0 0 0 / 6%)", "radius": "3px"},
    "link": {"decoration": "underline dotted"},
}

DECLARATIONS = (
    "  --steppy-strong-weight: 600;\n"
    "  --steppy-strong-color: #9a3412;\n"
    "  --steppy-em-style: normal;\n"
    "  --steppy-code-font: 'JetBrains Mono', monospace;\n"
    "  --steppy-code-bg: rgb(0 0 0 / 6%);\n"
    "  --steppy-code-radius: 3px;\n"
    "  --steppy-link-decoration: underline dotted;\n"
)


def _outputs(root):
    out = root / "output" / "generated"
    return {f.relative_to(out).as_posix(): f.read_bytes() for f in out.rglob("*") if f.is_file()}


# ── the model ────────────────────────────────────────────────────────────────


def test_declarations_come_in_a_fixed_order_and_only_when_set():
    styles = TextStylesConfig(**STYLES)
    assert text_styles_css(styles, ":root", rules=False) == ":root {\n" + DECLARATIONS + "}\n"


@pytest.mark.parametrize(
    "value",
    ["red; color: blue", "red}", "{", "</style><script>", "a\\62", "red /* x", "x */", "a\nb", "`${x}`"],
)
def test_a_value_that_could_escape_its_declaration_is_refused(value):
    with pytest.raises(ValidationError, match="textStyles.strong.color must be one CSS value"):
        TextStylesConfig(strong={"color": value})


@pytest.mark.parametrize("bad", [{"strong": {"size": "2em"}}, {"em": {"style": "bold"}}, {"quote": {}}])
def test_unknown_keys_and_values_are_refused(bad):
    with pytest.raises(ValidationError):
        TextStylesConfig(**bad)


def test_a_bare_number_is_only_a_weight():
    assert TextStylesConfig(strong={"weight": 700}).strong.weight == "700"
    with pytest.raises(ValidationError):
        TextStylesConfig(code={"radius": 3})


def test_only_the_configured_elements_get_rules():
    css = text_styles_css(TextStylesConfig(em={"color": "teal"}), ".steppy-guide")
    assert ".steppy-guide .steppy-step-content em {" in css
    assert "strong {" not in css and "code {" not in css and " a {" not in css


def test_unset_or_empty_emits_nothing():
    assert text_styles_css(None, ":root") == ""
    assert text_styles_css(TextStylesConfig(), ":root") == ""


# ── the build ────────────────────────────────────────────────────────────────


def test_static_build_appends_the_block_to_guides_css_on_root(tmp_path):
    config, root = _setup_project(tmp_path, target="static", textStyles=STYLES)
    BuildAssembler(config, root).build_all(handbook=True)
    css = (root / "output" / "generated" / "assets" / "guides.css").read_text(encoding="utf-8")
    block = css[css.index("/* textStyles") :]
    assert ":root {\n" + DECLARATIONS + "}\n" in block
    assert ".steppy-guide .steppy-step-content strong { font-weight: var(--steppy-strong-weight, bolder);" in block
    assert ".steppy-guide .steppy-step-content a { color: var(--steppy-link-color, LinkText);" in block
    # The handbook inlines the same stylesheet.
    book = (root / "output" / "generated" / "handbook" / "fr.html").read_text(encoding="utf-8")
    assert DECLARATIONS in book


def test_angular_build_scopes_the_block_to_the_fragment_and_the_player_host(tmp_path):
    guides = [
        {
            "slug": "onboarding",
            "title": "Onboarding",
            "scenario": "onboarding.md",
            "modes": ["stepbystep", "interactive"],
        }
    ]
    config, root = _setup_project(tmp_path, textStyles=STYLES, guides=guides)
    BuildAssembler(config, root).build_all()
    out = root / "output" / "generated"
    content = next(out.rglob("onboarding.content.ts")).read_text(encoding="utf-8")
    assert ".steppy-guide {" in content and ":root {" not in content
    assert "--steppy-code-radius: 3px;" in content
    interactive = next(out.rglob("onboarding-interactive.component.ts")).read_text(encoding="utf-8")
    # ViewEncapsulation.None makes :host match nothing: the properties go on the player element.
    assert "    .steppy-guide-host {\n" + DECLARATIONS.replace("  --", "      --") + "    }\n" in interactive
    assert "steppy-step-content" not in interactive  # the player brings its own rules


@pytest.mark.parametrize("target", ["static", "angular"])
def test_without_text_styles_the_output_mentions_none_of_it(tmp_path, target):
    config, root = _setup_project(tmp_path, target=target)
    BuildAssembler(config, root).build_all()
    for name, data in _outputs(root).items():
        if name.endswith((".css", ".html", ".ts")):
            assert b"textStyles" not in data and b"--steppy-strong" not in data, name


def test_text_styles_change_nothing_but_the_styles(tmp_path):
    plain, root_a = _setup_project(tmp_path / "a", target="static")
    styled, root_b = _setup_project(tmp_path / "b", target="static", textStyles=STYLES)
    BuildAssembler(plain, root_a).build_all()
    BuildAssembler(styled, root_b).build_all()
    a, b = _outputs(root_a), _outputs(root_b)
    assert a.keys() == b.keys()
    assert [name for name in a if a[name] != b[name]] == ["assets/guides.css"]
