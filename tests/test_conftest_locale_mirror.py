"""The locale logic the generated conftest cannot import, and its guard.

``templates/conftest.py.j2`` is standalone: a generated test suite runs under a
plain pytest with no ``steppy`` on the path, so anything it needs from the
package has to be copied into it. Two locale-shaped things are:

  * ``_base_language`` — byte-mirrored from ``steppy.locales.base_language``,
    because a regional locale (``fr-BE``) has to resolve an i18n value declared
    under its bare language (``fr``), exactly as ``GuideText.get`` and
    ``pick_locale`` do on the build side;
  * ``_apply_locale_init_script``'s fallback-then-warn behaviour, which had no
    test at all — and whose silent-skip predecessor is the reason a regional run
    could capture every screenshot in the app's *default* language and file them
    under the regional directory with nothing reporting a problem.

Mirrors the house pattern in ``tests/test_captures.py`` and ``tests/test_totp.py``.
"""

import re
import warnings
from pathlib import Path

import pytest

from steppy.locales import base_language

_CONFTEST = Path(__file__).parent.parent / "templates" / "conftest.py.j2"


def _template_source() -> str:
    return _CONFTEST.read_text(encoding="utf-8")


def _extract(name: str) -> str:
    """The source of one top-level ``def`` in the conftest template."""
    src = _template_source()
    marker = f"\ndef {name}("
    assert marker in src, f"could not locate `def {name}(` in conftest.py.j2"
    body = src.split(marker, 1)[1]
    rest = body.split("\n\ndef ", 1)[0]
    return f"def {name}(" + rest


def _load(name: str, namespace: dict) -> object:
    exec(_extract(name), namespace)
    return namespace[name]


# ── The byte mirror ──────────────────────────────────────────────────────────


def test_base_language_mirrors_the_conftest_template():
    """Both copies pick the i18n key for a regional locale; a drift means the
    capture side and the build side disagree about which language a screenshot
    is in."""
    template_fn = _load("_base_language", {})

    for tag in ("fr", "fr-BE", "fr_BE", "FR_be", "zh-Hans-CN", "pt-BR", "en-GB-oxendict", ""):
        assert base_language(tag) == template_fn(tag), (
            "steppy.locales.base_language has drifted from _base_language in "
            "templates/conftest.py.j2 — both resolve the same fallback and must stay identical"
        )


def test_the_mirror_is_declared_in_both_files():
    """A mirrored pair that does not say so gets un-mirrored by the next
    reader."""
    assert "Byte-mirrored" in _extract("_base_language")
    locales_src = (Path(__file__).parent.parent / "src" / "steppy" / "locales.py").read_text(encoding="utf-8")
    assert "conftest.py.j2" in locales_src


# ── The init script's fallback and warning ───────────────────────────────────


class _Context:
    """Just enough BrowserContext to record what would be injected."""

    def __init__(self):
        self.scripts: list = []

    def add_init_script(self, script):
        self.scripts.append(script)


@pytest.fixture
def init_script():
    """`_apply_locale_init_script` lifted out of the template, with a CONFIG of
    our choosing."""
    import json

    namespace: dict = {"json": json, "warnings": warnings, "BrowserContext": _Context}
    _load("_base_language", namespace)
    _load("_warn_locale_not_switchable", namespace)
    namespace["_LOCALE_NOT_SWITCHABLE"] = set()
    fn = _load("_apply_locale_init_script", namespace)

    def run(locale, values, key="user-language"):
        namespace["CONFIG"] = {"apps": {"editor": {"i18n": {"localstorage": {"key": key, "values": values}}}}}
        namespace["_LOCALE_NOT_SWITCHABLE"].clear()
        ctx = _Context()
        fn(ctx, "editor", locale)
        return ctx.scripts

    return run


class TestLocaleInitScript:
    def test_exact_key_wins(self, init_script):
        scripts = init_script("fr", {"fr": "fr_BE", "nl": "nl_BE"})
        assert len(scripts) == 1
        assert '"fr_BE"' in scripts[0]

    def test_regional_locale_falls_back_to_its_language(self, init_script):
        """The case that made `--locales fr-BE` capture French-labelled
        directories full of default-language screenshots."""
        scripts = init_script("fr-BE", {"fr": "fr_BE", "nl": "nl_BE"})
        assert len(scripts) == 1
        assert '"fr_BE"' in scripts[0]

    def test_a_regional_entry_still_beats_its_language(self, init_script):
        scripts = init_script("fr-CA", {"fr": "fr_BE", "fr-CA": "fr_CA"})
        assert '"fr_CA"' in scripts[0]

    def test_no_entry_at_all_warns_and_injects_nothing(self, init_script):
        with pytest.warns(UserWarning, match="was not switched into it"):
            scripts = init_script("de", {"fr": "fr_BE", "nl": "nl_BE"})
        assert scripts == []

    def test_the_warning_names_what_to_fix(self, init_script):
        with pytest.warns(UserWarning) as record:
            init_script("de", {"fr": "fr_BE"})
        message = str(record[0].message)
        assert "apps.editor.i18n.localstorage.values" in message
        assert "'de'" in message
        assert "config.yaml" in message

    def test_warns_once_per_role_and_locale(self, init_script):
        """A 40-scenario run should report a config gap once, not 40 times."""
        import json

        namespace: dict = {
            "json": json,
            "warnings": warnings,
            "BrowserContext": _Context,
            "_LOCALE_NOT_SWITCHABLE": set(),
        }
        _load("_base_language", namespace)
        _load("_warn_locale_not_switchable", namespace)
        namespace["CONFIG"] = {"apps": {"editor": {"i18n": {"localstorage": {"key": "k", "values": {"fr": "fr"}}}}}}
        fn = _load("_apply_locale_init_script", namespace)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for _ in range(5):
                fn(_Context(), "editor", "de")
        assert len(caught) == 1

    def test_no_locale_is_still_a_no_op(self, init_script):
        assert init_script(None, {"fr": "fr_BE"}) == []

    def test_no_i18n_block_is_still_a_silent_no_op(self, init_script):
        """Not every app switches language through localStorage; an absent block
        is a configuration choice, not a gap."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert init_script("fr", {}, key=None) == []


def test_the_template_no_longer_skips_silently():
    """Pins the shape rather than the behaviour: the old single-expression
    bail (`if not key or locale not in values: return`) is what made the bug
    invisible, and it is an easy thing to 'simplify' back to."""
    body = _extract("_apply_locale_init_script")
    assert "if not key or locale not in values" not in body
    assert "_warn_locale_not_switchable" in body
    assert re.search(r"_base_language\(locale\)", body)
