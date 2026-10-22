"""BCP-47 locale-tag grammar (``src/steppy/locales.py``).

Pure unit tests -- no build, no tmp_path, no Jinja. The module has two jobs and
both are pinned here: normalising an authored tag into the form ``<html lang>``
and ``hreflang`` need, and refusing a tag that has no correct form at all.

The reject vectors are the interesting half. Four of them (``index``, ``video``,
``interactive``, ``x-default``) are not typos -- they are the filenames and the
hreflang sentinel a locale is written beside, and the 2-3-letter bound on the
language subtag is what makes those collisions unrepresentable rather than
silently overwritten by ``_write_file``.
"""

import pytest

from steppy.build import LOCALE_LABELS
from steppy.locales import LocaleSyntaxError, base_language, normalize_locale, og_locale
from steppy.models import DEFAULT_LOCALES


# ── Accepted ─────────────────────────────────────────────────────────────────

ACCEPT = [
    ("fr", "fr"),
    ("FR", "fr"),
    ("  fr  ", "fr"),
    ("fr_BE", "fr-BE"),
    ("fr-be", "fr-BE"),
    ("FR_be", "fr-BE"),
    ("fr-BE", "fr-BE"),
    ("pt-BR", "pt-BR"),
    ("pt_br", "pt-BR"),
    ("nb", "nb"),
    ("fil", "fil"),
    ("FIL", "fil"),
    ("zh-Hant", "zh-Hant"),
    ("zh-hant", "zh-Hant"),
    ("ZH-HANT", "zh-Hant"),
    ("zh-Hans-CN", "zh-Hans-CN"),
    ("zh_hans_cn", "zh-Hans-CN"),
    ("sr-Latn-RS", "sr-Latn-RS"),
    ("sr_latn_rs", "sr-Latn-RS"),
    ("es-419", "es-419"),
    ("es_419", "es-419"),
    ("de-CH-1901", "de-CH-1901"),
    ("de-ch-1901", "de-CH-1901"),
    ("en-GB-oxendict", "en-GB-oxendict"),
    ("en-GB-OXENDICT", "en-GB-oxendict"),
    ("ca-valencia", "ca-valencia"),
    ("ca-VALENCIA", "ca-valencia"),
    # Two registered variants on one tag.
    ("sl-rozaj-biske", "sl-rozaj-biske"),
    # RFC 5646's own four-subtag example.
    ("hy-Latn-IT-arevela", "hy-Latn-IT-arevela"),
    # Android-style pseudo-locale: a normal region, nothing special needed.
    ("en-XA", "en-XA"),
    # The workaround the Windows-reserved message offers must actually work.
    ("con-EC", "con-EC"),
    # Documented quirk: a 4-letter subtag reads as a script, so the Windows
    # pseudo-locale is Titlecased. Harmless -- the authored form is untouched.
    ("qps-ploc", "qps-Ploc"),
]


class TestAccepted:
    @pytest.mark.parametrize(("tag", "expected"), ACCEPT, ids=[t for t, _ in ACCEPT])
    def test_normalises(self, tag: str, expected: str):
        assert normalize_locale(tag) == expected

    @pytest.mark.parametrize(("tag", "expected"), ACCEPT, ids=[t for t, _ in ACCEPT])
    def test_idempotent(self, tag: str, expected: str):
        """Call sites may normalise twice without thinking about it."""
        assert normalize_locale(expected) == expected


# ── Rejected ─────────────────────────────────────────────────────────────────

REJECT = [
    ("", "non-empty"),
    ("   ", "non-empty"),
    ("e", "2 or 3 letters"),
    ("engl", "2 or 3 letters"),
    ("english", "2 or 3 letters"),
    # The four collisions the language bound exists to make unrepresentable.
    ("index", "2 or 3 letters"),
    ("video", "2 or 3 letters"),
    ("interactive", "2 or 3 letters"),
    ("x-default", "private-use"),
    ("x-pseudo", "private-use"),
    ("fr-BE-x-old", "private-use"),
    ("de-DE-u-co-phonebk", "extension"),
    ("i-klingon", "tlh"),
    ("en-GB-oed", "oxendict"),
    ("zh-min-nan", "nan"),
    ("art-lojban", "jbo"),
    ("no-bok", "nb"),
    ("cel-gaulish", "no modern replacement"),
    ("zh-yue", "yue"),
    ("ar-arz", "arz"),
    ("con", "reserved device name"),
    ("CON", "reserved device name"),
    ("nul", "reserved device name"),
    ("fr-", "empty subtag"),
    ("-fr", "empty subtag"),
    ("fr--BE", "empty subtag"),
    ("fr_", "empty subtag"),
    ("fr.BE", "2 or 3 letters"),
    ("fr BE", "2 or 3 letters"),
    ("fr/BE", "2 or 3 letters"),
    ("../etc", "2 or 3 letters"),
    ("fr-BE-1901-1901", "more than once"),
    ("a" * 65, "too long"),
    ("fr-Latn-Latn", "invalid locale"),
    ("fr-BE-CA", "invalid locale"),
]


class TestRejected:
    @pytest.mark.parametrize(("tag", "fragment"), REJECT, ids=[t[:24] for t, _ in REJECT])
    def test_raises_with_an_actionable_message(self, tag: str, fragment: str):
        with pytest.raises(LocaleSyntaxError, match=fragment):
            normalize_locale(tag)

    @pytest.mark.parametrize("value", [123, None, ["fr"], {"fr": 1}, 1.5], ids=repr)
    def test_non_string(self, value):
        with pytest.raises(LocaleSyntaxError, match="must be strings"):
            normalize_locale(value)


# ── The rest of the repo agrees with the grammar ─────────────────────────────


class TestRepoConsistency:
    """Every locale this repo already ships must survive the new validator.

    Catches the shape of mistake where a label or a default is added in a
    spelling the grammar would reject at config load.
    """

    @pytest.mark.parametrize("code", sorted(LOCALE_LABELS))
    def test_builtin_label_keys_round_trip(self, code: str):
        assert normalize_locale(code) == code

    @pytest.mark.parametrize("code", sorted(DEFAULT_LOCALES))
    def test_default_locales_round_trip(self, code: str):
        assert normalize_locale(code) == code


# ── Projections ──────────────────────────────────────────────────────────────


class TestBaseLanguage:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [("fr", "fr"), ("fr-BE", "fr"), ("FR_be", "fr"), ("zh-Hans-CN", "zh"), ("hy-Latn-IT-arevela", "hy")],
    )
    def test_strips_to_the_language_subtag(self, tag: str, expected: str):
        assert base_language(tag) == expected

    def test_is_lenient_and_never_raises(self):
        """Its callers are content fallbacks -- raising there would turn a
        missing translation into a crash."""
        assert base_language("") == ""
        assert base_language("!!!") == "!!!"


class TestOgLocale:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("fr-BE", "fr_BE"),
            ("nl-BE", "nl_BE"),
            ("pt-BR", "pt_BR"),
            # Open Graph is language_TERRITORY only: no bare language, no
            # script, no numeric region. Omitting beats emitting an invalid one.
            ("fr", None),
            ("zh-Hant", None),
            ("es-419", None),
            ("zh-Hans-CN", None),
            ("de-CH-1901", None),
        ],
    )
    def test_strict_ll_cc_or_omitted(self, tag: str, expected):
        assert og_locale(tag) == expected
