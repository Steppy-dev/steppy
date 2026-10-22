"""BCP-47 language tags — the grammar behind ``lang`` and ``hreflang``.

A build's ``locales`` list lives a double life, and this module exists to keep
the two halves from being confused for one another.

The **authored** tag — exactly as written in ``steppy.guide.config.ts`` — is an
identity in three systems Steppy does not own:

  * the capture directories on disk, whose names were written by a *different*
    config file (``config.yaml``'s ``locales``, or ``steppy run --locales``) on
    a different machine, and which may already be committed under an assets
    tree;
  * the host application's i18n language code — the Angular target emits
    ``CONTENT['<locale>']`` and the generated component indexes it with
    ngx-translate's ``currentLang``, and real projects run codes like ``fr_BE``;
  * the keys of every ``LocalizedText`` map and every scenario's ``guide-text:``
    block, written by hand.

The **normalized** tag serves exactly one consumer: the W3C/BCP-47 readers of
``<html lang>`` and ``hreflang``. So the authored form is never rewritten — not
the filenames, not the directory segments, not the lookup keys — and the
normalized form is only ever bound to a name that says what it is
(``html_lang``, ``hreflang``, ``og_locale``). Normalizing the authored form
"for consistency" would silently repoint every screenshot lookup and every
host-app translation key at a directory and a language that do not exist.

What this module *does* do at config load is refuse a tag that cannot be given a
correct ``hreflang`` at all. That matters more than it sounds: one malformed
``hreflang`` makes a search engine discard the **whole** alternates cluster,
silently, so a project mis-spelling a single locale loses the annotation for all
of them.

Stdlib-only leaf: it imports nothing from ``steppy``, so ``build_models.py`` can
import it on the eager path (like ``assertions`` and ``exclusive``) without
tripping the lazy-import guards.
"""

from __future__ import annotations

import re

# ── Errors ───────────────────────────────────────────────────────────────────


class LocaleSyntaxError(ValueError):
    """Raised when a configured locale is not a usable BCP-47 language tag."""


# ── Grammar ──────────────────────────────────────────────────────────────────

# A tag becomes a filename, so an unbounded `*variant` is an unbounded path
# component. Three variants is already more than any registered tag uses.
MAX_LOCALE_LEN = 64
MAX_VARIANTS = 3

# RFC 5646 langtag, minus extlang/extension/privateuse (all rejected below with a
# message naming the modern spelling).
#
# The 2-3 letter bound on the language subtag is a correctness control, not a
# style rule. The tag is written verbatim as `<slug>/<locale>.html`, in the same
# directory as `interactive.html` and `video.html`, and `_write_file` overwrites
# without complaining -- so a locale named `video` or `interactive` silently
# clobbers a guide page, or is clobbered by one, depending on emission order. The
# same bound keeps a locale away from `index.html` and from `x-default`, the
# sentinel `_hreflang_alternates` appends. Every registered language subtag is
# two or three letters (ISO 639-1/2/3/5); RFC 5646's 4-letter and 5-8-letter
# productions are reserved and nothing occupies them, so the bound rejects
# nothing real and makes all four collisions unrepresentable.
_TAG_RE = re.compile(
    r"^(?P<lang>[A-Za-z]{2,3})"
    r"(?:-(?P<script>[A-Za-z]{4}))?"
    r"(?:-(?P<region>[A-Za-z]{2}|[0-9]{3}))?"
    rf"(?P<variants>(?:-(?:[A-Za-z0-9]{{5,8}}|[0-9][A-Za-z0-9]{{3}})){{0,{MAX_VARIANTS}}})$"
)

_EXTLANG_RE = re.compile(r"^[A-Za-z]{3}$")

# Reserved on Windows for the basename *before* the extension, so `con.html` is
# un-creatable just as `con` is -- and `con` is a real language, Cofan
# (ISO 639-3). The check runs against the whole normalized tag rather than its
# language subtag, because `con-EC.html` is a perfectly good filename and is the
# workaround the error message offers. Same guard, same reasoning, as
# `exclusive._WINDOWS_RESERVED`.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)

# Grandfathered tags, irregular and regular alike. Every one is deprecated in the
# IANA registry; those with a registered Preferred-Value map to it, the rest have
# no modern spelling and are simply refused.
_DEPRECATED: dict[str, str] = {
    "art-lojban": "jbo",
    "cel-gaulish": "",
    "en-gb-oed": "en-GB-oxendict",
    "i-ami": "ami",
    "i-bnn": "bnn",
    "i-default": "",
    "i-enochian": "",
    "i-hak": "hak",
    "i-klingon": "tlh",
    "i-lux": "lb",
    "i-mingo": "",
    "i-navajo": "nv",
    "i-pwn": "pwn",
    "i-tao": "tao",
    "i-tay": "tay",
    "i-tsu": "tsu",
    "no-bok": "nb",
    "no-nyn": "nn",
    "sgn-be-fr": "sfb",
    "sgn-be-nl": "vgt",
    "sgn-ch-de": "sgg",
    "zh-guoyu": "cmn",
    "zh-hakka": "hak",
    "zh-min": "",
    "zh-min-nan": "nan",
    "zh-xiang": "hsn",
}

_SHAPE_HELP = (
    "Use a BCP-47 language tag: a 2- or 3-letter language, optionally followed by a "
    "4-letter script, a 2-letter or 3-digit region, and variant subtags: 'fr', 'fr-BE', "
    "'pt-BR', 'zh-Hant', 'es-419', 'de-CH-1901'. Separators may be '-' or '_' and case "
    "is free."
)

_PRIVATE_USE_HELP = (
    "private-use ('x-') subtags are not supported. They carry no meaning for hreflang or "
    "<html lang>, and 'x-default' is already the hreflang fallback this build emits for "
    "the default locale."
)

_LANGUAGE_BOUND_HELP = (
    "must be 2 or 3 letters. Every registered language subtag is, and the bound is what "
    "keeps a locale from colliding with the 'index.html', 'video.html' and "
    "'interactive.html' pages it is written beside."
)


# ── Normalisation ────────────────────────────────────────────────────────────


def normalize_locale(value: object) -> str:
    """Validate one authored locale tag and return its canonical BCP-47 form.

    ``fr_BE`` -> ``fr-BE``, ``FR`` -> ``fr``, ``zh-hant-cn`` -> ``zh-Hant-CN``.
    The result is what belongs in ``<html lang>`` and ``hreflang``; the caller
    keeps the authored spelling for filenames and lookup keys (see the module
    docstring).

    Raises:
        LocaleSyntaxError: with an actionable message for every rejection,
            naming the modern spelling wherever one exists.
    """
    if not isinstance(value, str):
        raise LocaleSyntaxError(f"locales entries must be strings, got {type(value).__name__}")

    tag = value.strip()
    if not tag:
        raise LocaleSyntaxError("a locale must be a non-empty language tag. " + _SHAPE_HELP)
    if len(tag) > MAX_LOCALE_LEN:
        raise LocaleSyntaxError(
            f"locale {tag!r} is too long ({len(tag)} characters, limit {MAX_LOCALE_LEN}). "
            "The tag is written verbatim into page filenames and screenshot directories."
        )

    folded = tag.replace("_", "-")
    subtags = folded.split("-")
    if any(not part for part in subtags):
        raise LocaleSyntaxError(f"locale {tag!r} has an empty subtag. " + _SHAPE_HELP)

    lowered = folded.lower()
    if lowered in _DEPRECATED:
        preferred = _DEPRECATED[lowered]
        if preferred:
            raise LocaleSyntaxError(f"locale {tag!r} is a deprecated tag. Use {preferred!r} instead.")
        raise LocaleSyntaxError(
            f"locale {tag!r} is a deprecated tag with no modern replacement. "
            "Pick a current ISO 639 language code instead."
        )

    for index, part in enumerate(subtags):
        if len(part) != 1:
            continue
        if part.lower() == "x":
            raise LocaleSyntaxError(f"locale {tag!r}: " + _PRIVATE_USE_HELP)
        if index:
            raise LocaleSyntaxError(
                f"locale {tag!r}: extension subtags such as '-{part.lower()}-' are not supported. "
                "They carry no meaning for hreflang or <html lang>."
            )

    match = _TAG_RE.match(folded)
    if match is None:
        # An extlang form (`zh-yue`, `ar-arz`) is the one near-miss worth naming:
        # RFC 5646 asks for the primary subtag, so the fix is to drop the prefix.
        if len(subtags) == 2 and _EXTLANG_RE.match(subtags[1]):
            raise LocaleSyntaxError(
                f"locale {tag!r} looks like an extended language subtag. Use the language "
                f"itself, {subtags[1].lower()!r}, instead."
            )
        if len(subtags[0]) not in (2, 3) or not subtags[0].isalpha():
            raise LocaleSyntaxError(
                f"invalid locale {tag!r}: the language subtag {subtags[0]!r} " + _LANGUAGE_BOUND_HELP
            )
        raise LocaleSyntaxError(f"invalid locale {tag!r}. " + _SHAPE_HELP)

    variants = [part for part in match.group("variants").split("-") if part]
    seen: set[str] = set()
    for variant in variants:
        key = variant.lower()
        if key in seen:
            raise LocaleSyntaxError(f"invalid locale {tag!r}: the variant subtag {variant!r} appears more than once.")
        seen.add(key)

    parts = [match.group("lang").lower()]
    if match.group("script"):
        parts.append(match.group("script").title())
    region = match.group("region")
    if region:
        parts.append(region.upper() if region.isalpha() else region)
    parts.extend(variant.lower() for variant in variants)
    normalized = "-".join(parts)

    if normalized.lower() in _WINDOWS_RESERVED:
        raise LocaleSyntaxError(
            f"locale {tag!r} normalises to {normalized!r}, a reserved device name on Windows: the "
            f"build writes '<slug>/{normalized}.html', and that filename cannot be created there. "
            f"Use a region-qualified tag such as '{normalized}-EC' instead."
        )

    return normalized


def validate_locale_tag(value: object) -> None:
    """Raise ``ValueError`` if ``value`` is not a usable BCP-47 tag.

    Validates only — the authored spelling is never rewritten (see the module
    docstring for why). Lives here rather than in either model module because
    both sides of the repo validate locales now: ``models.SteppyConfig`` for the
    capture side (``config.yaml``) and ``build_models.BuildConfig`` for the build
    side. Re-raised as a plain ``ValueError`` so pydantic reports it as a field
    error rather than an unhandled exception.
    """
    try:
        normalize_locale(value)
    except LocaleSyntaxError as exc:
        raise ValueError(str(exc)) from exc


def base_language(tag: str) -> str:
    """The bare language subtag of ``tag``, lowercased (``fr-BE`` -> ``fr``).

    Deliberately lenient — it never validates and never raises. Its callers are
    content-resolution fallbacks (``pick_locale``, ``GuideText.get``), where
    turning a missing translation into an exception would be strictly worse than
    falling through to the default locale.

    **Byte-mirrored** as ``_base_language`` in ``templates/conftest.py.j2``: the
    generated test suite is standalone and cannot import steppy, but it needs the
    same rule to pick an i18n value for a regional locale. Guard:
    ``tests/test_conftest_locale_mirror.py``. Apply any change to both.
    """
    return tag.replace("_", "-").split("-", 1)[0].lower()


def og_locale(tag: str) -> str | None:
    """Open Graph ``og:locale`` for a normalized tag, or ``None`` to omit it.

    The Open Graph protocol defines the value as ``language_TERRITORY`` and
    Facebook validates it against a finite list, every entry of which is a
    2-letter language plus a 2-letter country. So a script or a numeric region
    has no valid spelling (``zh-Hant`` is not ``zh_Hant``), and neither does a
    region-less tag — ``fr`` was never a valid ``og:locale``, and inventing
    ``fr_FR`` for it would be a guess this project cannot make (Steppy's own
    demo is Belgian). Omitting and emitting an invalid value produce the same
    consumer behaviour, and omitting does not lie.
    """
    parts = tag.split("-")
    if len(parts) != 2 or len(parts[1]) != 2 or not parts[1].isalpha():
        return None
    return f"{parts[0].lower()}_{parts[1].upper()}"
