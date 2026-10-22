"""Config-only locales (FLAWS §4.4).

Adding a language must not require a model edit:
- a ``guide-text`` block accepts any locale key (previously only fr/en/nl/de);
- the build-time locale-label lookup has a graceful fallback and a config
  override.
"""

from pathlib import Path

from steppy.models import GuideText, GuideTextContent, DEFAULT_LOCALES
from steppy.parser import parse_scenario_file


def test_guide_text_accepts_arbitrary_locale():
    gt = GuideText(**{"es": "Hola. Mundo.", "it": {"title": "Ciao", "text": "Ciao a tutti"}})
    assert isinstance(gt.get("es"), GuideTextContent)
    assert gt.get("es").title == "Hola"
    assert gt.get("it").title == "Ciao"
    # Declared locales still work and unknown ones are None.
    assert gt.get("fr") is None


def test_guide_text_declared_fields_still_work():
    gt = GuideText(**{"fr": {"title": "Bonjour", "subtitle": "s", "text": "t"}})
    assert gt.fr.title == "Bonjour"  # attribute back-compat
    assert gt.get("fr").subtitle == "s"  # accessor parity


def test_scenario_with_spanish_guide_text(tmp_path: Path):
    """A scenario declaring an es guide-text parses (was silently dropped)."""
    scn = tmp_path / "s.md"
    scn.write_text(
        "---\ntitle: T\n---\n\n"
        "### 1. Click\n- role: admin\n- action: click\n- selector: '#x'\n"
        "- guide-text:\n    es: Haga clic aquí\n    fr: Cliquez ici\n",
        encoding="utf-8",
    )
    parsed = parse_scenario_file(scn)
    step = parsed.steps[0]
    assert step.guide_text.get("es").title == "Haga clic aquí"
    assert step.guide_text.get("fr").title == "Cliquez ici"


def test_default_locales_constant_is_single_source():
    from steppy.models import SteppyConfig

    cfg = SteppyConfig(apps={"admin": {"name": "Admin", "url": "http://localhost", "auth": {"mode": "bypass"}}})
    assert cfg.locales == DEFAULT_LOCALES
    assert cfg.default_locale == "fr"


def test_locale_label_fallback_uppercases_unknown_code():
    from steppy.build import LOCALE_LABELS

    # Newly-broadened built-ins cover common languages.
    assert LOCALE_LABELS["es"] == "Español"
    # Unknown codes are handled by the assembler's .get(loc, loc.upper())
    # fallback — assert the map itself simply has no entry.
    assert "xx" not in LOCALE_LABELS


# ── config.yaml locales are BCP-47 too (parity with the build side) ─────────


class TestConfigLocaleTags:
    """`steppy build` has validated its `locales` since the hreflang work. The
    capture side writes the same strings as screenshot directory segments that
    the build later reads back, so leaving it unchecked meant the two halves of
    one project could disagree about what a locale is — and the disagreement
    surfaced as missing captures rather than as an error.
    """

    def _config(self, **overrides):
        from steppy.models import SteppyConfig

        return SteppyConfig(
            apps={"admin": {"name": "Admin", "url": "http://localhost", "auth": {"mode": "bypass"}}},
            **overrides,
        )

    def test_accepts_a_regional_tag_verbatim(self):
        """Validated, not rewritten: the authored spelling is a join key with
        the capture directories and with the app's own i18n codes."""
        cfg = self._config(locales=["fr_BE", "nl-be"], default_locale="fr_BE")
        assert cfg.locales == ["fr_BE", "nl-be"]

    def test_rejects_an_unusable_locale(self):
        import pytest

        with pytest.raises(ValueError, match="2 or 3 letters"):
            self._config(locales=["fr", "index"], default_locale="fr")

    def test_rejects_an_unusable_default_locale(self):
        import pytest

        with pytest.raises(ValueError, match="private-use"):
            self._config(locales=["fr"], default_locale="x-default")

    def test_default_locale_membership_still_checked(self):
        import pytest

        with pytest.raises(ValueError, match="must be in locales list"):
            self._config(locales=["fr"], default_locale="nl")


# ── Regional locales resolve through their language ─────────────────────────


class TestRegionalLocaleFallback:
    """`GuideText.get` is the one home for this, which is what makes the two
    consumers agree: `prepare_guide_steps` used to raise "Missing guide-text for
    locale 'fr-BE'" and stop the build, while `prepare_interactive_manifest`
    silently emitted nulls for the same miss.
    """

    def test_region_falls_back_to_language(self):
        gt = GuideText(**{"fr": "Bonjour. Le monde.", "nl": "Hallo."})
        assert gt.get("fr-BE").title == "Bonjour"
        assert gt.get("nl-BE").title == "Hallo"

    def test_underscore_form_too(self):
        """The spelling real host apps use, and the one `--locales fr_BE` writes
        as a directory name."""
        gt = GuideText(**{"fr": "Bonjour."})
        assert gt.get("fr_BE").title == "Bonjour"

    def test_an_exact_regional_key_wins(self):
        gt = GuideText(**{"fr": "Générique.", "fr-BE": "Belge."})
        assert gt.get("fr-BE").title == "Belge"
        assert gt.get("fr-CA").title == "Générique"

    def test_the_query_is_truncated_never_widened(self):
        """`fr` must not match a block declaring only `fr-BE`: with `fr-BE` and
        `fr-CA` both present the choice would be arbitrary."""
        gt = GuideText(**{"fr-BE": "Belge."})
        assert gt.get("fr") is None

    def test_an_unrelated_locale_is_still_none(self):
        gt = GuideText(**{"fr": "Bonjour."})
        assert gt.get("de-AT") is None
