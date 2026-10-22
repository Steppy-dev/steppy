"""``sitemap.xml`` serialisation — the one place the XML is built.

Why this is a module and not a Jinja template, stated honestly, because the
obvious reason is the weak one:

  * **Escaping** buys less than it looks like it does. Jinja's ``| e`` and
    ``ElementTree`` behave identically on ``&``, ``<``, ``>`` and ``]]>``, and
    *both* pass control characters and NUL straight through into a document no
    parser will accept. The one real bug ET closes is an **attribute** that was
    interpolated with no filter at all: ``hreflang="{{ alt.hreflang }}"`` in the
    template this replaces, under an ``autoescape=False`` environment. Here
    escaping is unconditional rather than dependent on someone remembering a
    filter, and the characters neither engine can escape are **rejected** (see
    ``_XML_ILLEGAL``) instead of silently emitted.
  * The real reasons: a schema-bearing machine format should have a generator
    rather than a string template that any edit can push out of
    well-formedness; ``<lastmod>`` and future per-URL extensions become
    structural instead of textual; and the whole thing becomes a pure function
    testable with no build, no ``tmp_path`` and no Jinja environment.

A project that wants more than this emits — ``<image:image>`` over a guide's
screenshots, ``<video:video>``, a ``<sitemapindex>`` split — still overrides
``templates/static/sitemap.xml.j2`` and wins; see ``_write_static_sitemap``.

Stdlib-only leaf: imports nothing from ``steppy``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

# ── Errors ───────────────────────────────────────────────────────────────────


class SitemapError(ValueError):
    """Raised when a sitemap cannot be serialised as well-formed XML."""


# ── Constants ────────────────────────────────────────────────────────────────

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
XHTML_NS = "http://www.w3.org/1999/xhtml"

# ElementTree cannot produce this line. `encoding="utf-8"` emits single quotes
# and a lowercase name; `encoding="UTF-8"` suppresses the declaration entirely,
# because `ElementTree.write` only writes one when the encoding is *not*
# utf-8/us-ascii/unicode and "UTF-8".lower() is "utf-8". So it is prepended.
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'

# sitemaps.org protocol limits. Exceeding either makes a search engine reject the
# *whole* file rather than the excess, and nothing about that is visible locally.
MAX_SITEMAP_URLS = 50_000
MAX_SITEMAP_BYTES = 50 * 1024 * 1024

# XML 1.0 section 2.2 Char production. Anything outside it has no escaped
# spelling -- a character reference to it is itself illegal -- so it can only be
# rejected. Lone surrogates land here too (D800-DFFF is excluded), which is what
# keeps them from reaching `Path.write_text` as a UnicodeEncodeError far from the
# value that caused it.
_XML_ILLEGAL = re.compile(r"[^\x09\x0A\x0D\x20-퟿-�\U00010000-\U0010FFFF]")

# W3C Datetime, as the sitemap protocol cites it, narrowed to the two shapes
# anyone means. A timezone is mandatory whenever a time is present -- an
# offset-less "2026-09-14T12:00:00" is not a valid W3C datetime, and it is the
# trap `datetime.fromisoformat` accepts without complaint. Bare `YYYY` and
# `YYYY-MM` are deliberately dropped: nobody means them and crawlers handle them
# poorly.
_LASTMOD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))?$")

_LASTMOD_HELP = (
    "must be a W3C datetime: 'YYYY-MM-DD', or 'YYYY-MM-DDThh:mm:ssZ' / "
    "'YYYY-MM-DDThh:mm:ss+hh:mm' (a timezone is required when a time is given)"
)


# ── Value types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Alternate:
    """One ``<xhtml:link rel="alternate">`` row: a language and its URL."""

    hreflang: str
    href: str


@dataclass(frozen=True)
class SitemapUrl:
    """One ``<url>`` entry.

    ``lastmod`` is per-URL from day one even though the config sets a single
    site-wide value today: the version that earns its keep is a guide stamped
    with its own scenario's commit date, and leaving the field off would mean
    rewriting every call site to add it later.
    """

    loc: str
    alternates: tuple[Alternate, ...] = ()
    lastmod: Optional[str] = None


# ── Validation ───────────────────────────────────────────────────────────────


def _check_xml_text(value: str, field: str) -> str:
    """Reject text no XML document can carry, naming the field that carried it.

    Rejecting rather than stripping is the point: a control character in a
    ``loc`` means the URL was built from a bad slug, locale or path, and a
    silently-stripped one yields a URL that 404s from a sitemap that looks fine.
    """
    if not isinstance(value, str):
        raise SitemapError(f"sitemap {field} must be a string, got {type(value).__name__}")
    bad = _XML_ILLEGAL.search(value)
    if bad is not None:
        raise SitemapError(
            f"sitemap {field} contains a character XML cannot represent "
            f"(U+{ord(bad.group()):04X}) and therefore cannot be escaped: {value!r}"
        )
    return value


def normalize_lastmod(value: object) -> str:
    """Validate a ``<lastmod>`` value and return it unchanged.

    Shape is checked by regex and *then* by ``datetime.fromisoformat``, because
    neither is sufficient alone: the regex cannot tell that ``2026-02-31`` is
    not a day, and ``fromisoformat`` happily accepts ``20260914``,
    ``2026-W12-1``, a space separator, and a time with no timezone -- none of
    which are valid W3C datetimes.
    """
    if not isinstance(value, str):
        raise SitemapError(f"lastmod {_LASTMOD_HELP}, got {type(value).__name__}")
    text = value.strip()
    if not _LASTMOD_RE.match(text):
        raise SitemapError(f"lastmod {_LASTMOD_HELP}, got {value!r}")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SitemapError(f"lastmod {_LASTMOD_HELP}, got {value!r} ({exc})") from exc
    return text


def limit_warnings(url_count: int, byte_size: int) -> list[str]:
    """Protocol-limit warnings for a rendered sitemap, most severe first.

    Pure, so the leaf owns the thresholds and the wording while ``build.py``
    owns the console. Empty when the document is within both limits.
    """
    warnings: list[str] = []
    if url_count > MAX_SITEMAP_URLS:
        warnings.append(
            f"sitemap.xml lists {url_count:,} URLs, over the {MAX_SITEMAP_URLS:,}-URL sitemaps.org "
            "limit — search engines reject the whole file, not the excess. Reduce the built "
            "surface, or override templates/static/sitemap.xml.j2 to emit a <sitemapindex>."
        )
    if byte_size > MAX_SITEMAP_BYTES:
        warnings.append(
            f"sitemap.xml is {byte_size / 1024 / 1024:.1f} MB, over the "
            f"{MAX_SITEMAP_BYTES // 1024 // 1024} MB uncompressed sitemaps.org limit — search "
            "engines reject the whole file."
        )
    return warnings


# ── Serialisation ────────────────────────────────────────────────────────────


def render_sitemap(urls: Sequence[SitemapUrl] | Iterable[SitemapUrl], *, default_lastmod: Optional[str] = None) -> str:
    """Serialise ``urls`` as a sitemaps.org 0.9 document.

    Element order inside ``<url>`` is ``<loc>``, the ``xhtml:link`` alternates,
    then ``<lastmod>`` — the shape Google's own hreflang example uses, and the
    layout the Jinja template this replaces produced.
    """
    # Namespaces go in as ordinary attributes and the tags carry literal
    # prefixes, rather than building a namespace-aware tree. Two reasons, both
    # dead ends otherwise: `tostring(default_namespace=...)` raises outright
    # ("cannot use non-qualified names with default_namespace option") because
    # rel/hreflang/href are un-namespaced attributes; and `register_namespace`
    # mutates `ET._namespace_map` process-wide and permanently, which would make
    # any later XML-serialising test in the same pytest session order-dependent.
    # The output is byte-identical to the registered form. The tree is therefore
    # not namespace-aware, but it never leaves this function.
    root = ET.Element("urlset", {"xmlns": SITEMAP_NS, "xmlns:xhtml": XHTML_NS})
    count = 0
    for entry in urls:
        count += 1
        url_el = ET.SubElement(root, "url")
        ET.SubElement(url_el, "loc").text = _check_xml_text(entry.loc, "loc")
        for alt in entry.alternates:
            link = ET.SubElement(url_el, "xhtml:link")
            link.set("rel", "alternate")
            link.set("hreflang", _check_xml_text(alt.hreflang, "hreflang"))
            link.set("href", _check_xml_text(alt.href, "href"))
        lastmod = entry.lastmod if entry.lastmod is not None else default_lastmod
        if lastmod is not None:
            ET.SubElement(url_el, "lastmod").text = _check_xml_text(lastmod, "lastmod")

    ET.indent(root, space="  ")
    return f"{XML_DECLARATION}\n{ET.tostring(root, encoding='unicode')}\n"
