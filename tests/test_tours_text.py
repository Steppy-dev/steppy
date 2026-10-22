"""Per-locale tour text (``steppy.tours.text``)."""

import textwrap

from steppy.models import GuideTextContent, SteppyConfig
from steppy.parser import parse_scenario_file
from steppy.tours.compiler import compile_tours
from steppy.tours.settings import ToursSettings, settings_diagnostics
from steppy.tours.text import title_text


def _compile(tmp_path, body, frontmatter="tour: true", **tours):
    config = SteppyConfig(
        apps={"editor": {"name": "E", "url": "http://localhost:3000", "auth": {"mode": "bypass"}}},
        locales=["en", "fr", "nl"],
        default_locale="en",
        tours={"enabled": True, "targets": [{"type": "guide"}], **tours},
    )
    path = tmp_path / "editor--case.md"
    path.write_text(
        f"---\ntitle: Case\n{frontmatter}\n---\n\n### 1. Open\n- role: editor\n- action: navigate\n- target: /x\n"
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    return compile_tours([parse_scenario_file(path)], ToursSettings.from_config(config, tmp_path))


CLICK = "\n### 2. Go\n- role: editor\n- action: click\n- selector: '[data-testid=\"go\"]'\n"


def test_tour_text_wins_over_guide_text_per_locale_and_languages_never_mix(tmp_path):
    result = _compile(
        tmp_path,
        CLICK + '- guide-text:\n    en: "Guide en."\n    fr: "Guide fr."\n- tour-text:\n    en: "Tour en."\n',
    )
    (tour,) = result.tours
    assert tour.locales == ("en", "fr")  # nl has no authored text: not emitted
    assert tour.step_texts["en"][0] == (None, "Tour en.")
    assert tour.step_texts["fr"][0] == (None, "Guide fr.")


def test_a_locale_without_text_falls_to_the_generic_hint_with_a_warning(tmp_path):
    body = (
        CLICK
        + '- guide-text:\n    en: "Go."\n    fr: "Allez."\n'
        + "\n### 3. Next\n- role: editor\n- action: click\n- selector: '[data-testid=\"next\"]'\n"
        + '- guide-text:\n    en: "Next."\n'
    )
    result = _compile(tmp_path, body)
    (tour,) = result.tours
    assert tour.step_texts["fr"][1] == (None, None)
    warning = [d for d in result.diagnostics if d.code == "generic-text"]
    assert warning and "fr" in warning[0].message


def test_the_base_language_rung(tmp_path):
    result = _compile(
        tmp_path, CLICK + '- guide-text:\n    fr: "Allez."\n', locales=["en", "fr-BE"], default_locale="en"
    )
    assert result.tours[0].step_texts["fr-BE"][0] == (None, "Allez.")


def test_titles_and_legacy_strings():
    assert title_text(GuideTextContent(title="Do this", text="Do this. Then that.")) == (None, "Do this. Then that.")
    assert title_text(GuideTextContent(title="Save", text="Save the invoice")) == ("Save", "Save the invoice")
    assert title_text(GuideTextContent(title="Only a title")) == (None, "Only a title")
    assert title_text(None) == (None, None)


def test_tour_title_per_locale_with_fallback(tmp_path):
    result = _compile(
        tmp_path, CLICK + '- guide-text:\n    en: "Go."\n    fr: "Allez."\n', frontmatter="tour:\n  title: {fr: Titre}"
    )
    assert result.tours[0].titles == {"en": "Case", "fr": "Titre"}
    assert "title-fallback" in [d.code for d in result.diagnostics]


def test_guide_urls_need_a_base_url_and_a_guide(tmp_path):
    body = CLICK + '- guide-text:\n    en: "Go."\n'
    assert _compile(tmp_path, body).tours[0].guide_urls == {}
    with_slug = _compile(
        tmp_path, body, frontmatter="tour:\n  guide_slug: go", guide_base_url="https://guide.example.com/docs"
    )
    assert with_slug.tours[0].guide_urls == {"en": "https://guide.example.com/docs/go/en.html"}


def test_fixture_values_in_text_warn(tmp_path):
    body = (
        "\n### 2. Name\n- role: editor\n- action: fill\n- selector: '[data-testid=\"name\"]'\n"
        '- value: "Acme Corp"\n- guide-text:\n    en: "Type Acme Corp here."\n'
        "\n### 3. Amount\n- role: editor\n- action: fill\n- selector: '[data-testid=\"amount\"]'\n"
        '- value: "70"\n- guide-text:\n    en: "Type the amount (e.g. 1700)."\n'
    )
    leaks = [d for d in _compile(tmp_path, body).diagnostics if d.code == "fixture-leak"]
    assert len(leaks) == 1 and "Acme Corp" in leaks[0].message  # "70" inside "1700" is not a word match


def test_links_outside_routes_and_the_guide_warn(tmp_path):
    body = (
        CLICK
        + '- guide-text:\n    en: "See [help](/help), [guide](https://guide.example.com/x) or [web](https://evil.example)."\n'
    )
    dropped = [
        d
        for d in _compile(tmp_path, body, guide_base_url="https://guide.example.com").diagnostics
        if d.code == "text-link-dropped"
    ]
    assert len(dropped) == 1 and "evil.example" in dropped[0].message


def test_tour_text_for_an_untargeted_locale_warns(tmp_path):
    result = _compile(tmp_path, CLICK + '- tour-text:\n    en: "Go."\n    de: "Los."\n')
    assert "tour-text-locale-untargeted" in [d.code for d in result.diagnostics]


def test_text_limits_are_errors(tmp_path):
    long = "x" * 2001
    result = _compile(tmp_path, CLICK + f'- guide-text:\n    en: "{long}"\n')
    assert "limit-exceeded" in [d.code for d in result.diagnostics if d.severity == "error"]


def test_links_that_resolve_off_site_warn(tmp_path):
    # Dot segments collapse into a protocol-relative `//host` once a browser resolves them.
    body = (
        CLICK
        + '- guide-text:\n    en: "[a](/.//evil.example/x) [b](/a/..//evil.example) '
        + '[c](/%2e%2e//evil.example) [ok](/a/../help)"\n'
    )
    dropped = [d.message for d in _compile(tmp_path, body).diagnostics if d.code == "text-link-dropped"]
    assert len(dropped) == 3 and not any("/help" in m for m in dropped)


def test_a_tour_title_map_falls_back_to_the_default_locale_entry(tmp_path):
    result = _compile(
        tmp_path, CLICK + '- guide-text:\n    en: "Go."\n    fr: "Allez."\n', frontmatter="tour:\n  title: {en: Create}"
    )
    assert result.tours[0].titles == {"en": "Create", "fr": "Create"}
    (fallback,) = [d for d in result.diagnostics if d.code == "title-fallback"]
    assert "fr" in fallback.message and fallback.scenario == "editor--case"


def test_multi_language_tours_without_a_title_warn_once_for_the_project(tmp_path):
    result = _compile(tmp_path, CLICK + '- guide-text:\n    en: "Go."\n    fr: "Allez."\n')
    (fallback,) = [d for d in result.diagnostics if d.code == "title-fallback"]
    assert fallback.scenario is None and "1 multi-language tour" in fallback.message


def test_a_configured_locale_without_text_is_named(tmp_path):
    result = _compile(tmp_path, CLICK + '- guide-text:\n    en: "Go."\n    fr: "Allez."\n')
    (dropped,) = [d for d in result.diagnostics if d.code == "locale-not-emitted"]
    assert "nl" in dropped.message and "fr" not in dropped.message.split(";")[0]


def test_a_locale_key_that_differs_only_in_case_or_separator_suggests_the_tag(tmp_path):
    result = _compile(
        tmp_path,
        CLICK + '- tour-text:\n    en: "Go."\n    fr_be: "Allez."\n',
        locales=["en", "fr-BE"],
        default_locale="en",
    )
    hints = [d.message for d in result.diagnostics if d.code == "tour-text-locale-untargeted"]
    assert len(hints) == 1 and "did you mean 'fr-BE'" in hints[0]
    guide = _compile(tmp_path, CLICK + '- guide-text:\n    en: "Go."\n    FR: "Allez."\n')
    hints = [d.message for d in guide.diagnostics if d.code == "tour-text-locale-untargeted"]
    assert len(hints) == 1 and "guide-text" in hints[0] and "did you mean 'fr'" in hints[0]


def test_a_tour_locale_without_built_in_chrome_strings_warns(tmp_path):
    def diagnostics(locales):
        config = SteppyConfig(
            apps={"editor": {"name": "E", "url": "http://localhost:3000", "auth": {"mode": "bypass"}}},
            locales=locales,
            default_locale="en",
            tours={"enabled": True, "targets": [{"type": "guide"}]},
        )
        return [d for d in settings_diagnostics(config, ToursSettings.from_config(config, tmp_path))]

    missing = [d for d in diagnostics(["en", "es", "ar"]) if d.code == "chrome-strings-missing"]
    assert [d.message.split("'")[1] for d in missing] == ["es", "ar"]
    assert not [d for d in diagnostics(["en", "fr-BE", "de"]) if d.code == "chrome-strings-missing"]
