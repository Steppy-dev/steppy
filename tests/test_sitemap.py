"""sitemap.xml serialisation (``src/steppy/sitemap.py``).

Pure unit tests -- no build, no tmp_path, no Jinja. Three things are pinned that
a reader would otherwise be free to "simplify" back into a bug: the XML
declaration (ElementTree cannot produce the conventional spelling at all), the
absence of any process-global namespace registration, and the fact that illegal
characters are *rejected* rather than passed through the way the Jinja template
this replaces passed them.
"""

import xml.etree.ElementTree as ET

import pytest

from steppy.sitemap import (
    MAX_SITEMAP_BYTES,
    MAX_SITEMAP_URLS,
    SITEMAP_NS,
    XHTML_NS,
    Alternate,
    SitemapError,
    SitemapUrl,
    limit_warnings,
    normalize_lastmod,
    render_sitemap,
)

_NS = {"sm": SITEMAP_NS, "xhtml": XHTML_NS}

BASE = "https://x.example"

ONE_URL = SitemapUrl(
    f"{BASE}/index.html",
    (
        Alternate("fr", f"{BASE}/index.html"),
        Alternate("x-default", f"{BASE}/index.html"),
    ),
)

# The regression anchor. Any change in ElementTree's indentation, attribute
# ordering or empty-element spacing surfaces here first, in one diff.
ONE_URL_EXPECTED = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
    ' xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
    "  <url>\n"
    "    <loc>https://x.example/index.html</loc>\n"
    '    <xhtml:link rel="alternate" hreflang="fr" href="https://x.example/index.html" />\n'
    '    <xhtml:link rel="alternate" hreflang="x-default" href="https://x.example/index.html" />\n'
    "  </url>\n"
    "</urlset>\n"
)


# ── Serialisation shape ──────────────────────────────────────────────────────


class TestShape:
    def test_exact_bytes_one_url_two_alternates(self):
        assert render_sitemap([ONE_URL]) == ONE_URL_EXPECTED

    def test_declaration_is_the_conventional_spelling(self):
        """Double quotes and uppercase UTF-8 -- the one thing ElementTree cannot
        emit, hence the literal constant it is prepended from."""
        out = render_sitemap([ONE_URL])
        assert out.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
        assert out.endswith("</urlset>\n")
        assert not out.endswith("\n\n")

    def test_namespaces_parse_and_keep_their_prefixes(self):
        out = render_sitemap([ONE_URL])
        root = ET.fromstring(out)
        assert root.tag == f"{{{SITEMAP_NS}}}urlset"
        assert [e.tag for e in root.findall(".//sm:url/xhtml:link", _NS)] == [f"{{{XHTML_NS}}}link"] * 2
        # Semantics are not enough: the prefix spelling is part of the contract
        # for anyone grepping or diffing the file.
        assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in out
        assert "<xhtml:link " in out

    def test_does_not_mutate_the_global_namespace_map(self):
        """`ET.register_namespace` would work and would be permanent, making any
        later XML-serialising test in the session order-dependent. Keep it out."""
        before = dict(ET._namespace_map)
        render_sitemap([ONE_URL])
        assert dict(ET._namespace_map) == before
        assert SITEMAP_NS not in ET._namespace_map

    def test_element_order_within_url(self):
        entry = SitemapUrl(f"{BASE}/a.html", (Alternate("fr", f"{BASE}/a.html"),), lastmod="2026-09-14")
        root = ET.fromstring(render_sitemap([entry]))
        tags = [child.tag for child in root.find("sm:url", _NS)]
        assert tags == [f"{{{SITEMAP_NS}}}loc", f"{{{XHTML_NS}}}link", f"{{{SITEMAP_NS}}}lastmod"]

    def test_attribute_order_on_an_alternate(self):
        out = render_sitemap([ONE_URL])
        assert '<xhtml:link rel="alternate" hreflang="fr" href=' in out

    def test_no_alternates(self):
        out = render_sitemap([SitemapUrl(f"{BASE}/a.html")])
        assert "xhtml:link" not in out
        assert ET.fromstring(out).find("sm:url/sm:loc", _NS).text == f"{BASE}/a.html"

    def test_empty_url_list(self):
        """Unreachable from build.py (there is always at least one index URL),
        but part of the leaf's own API."""
        out = render_sitemap([])
        assert out.rstrip().endswith("/>")
        assert ET.fromstring(out).findall("sm:url", _NS) == []


# ── Determinism ──────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_byte_identical_across_calls(self):
        assert render_sitemap([ONE_URL]) == render_sitemap([ONE_URL])

    def test_byte_identical_across_fresh_input_objects(self):
        rebuilt = SitemapUrl(
            f"{BASE}/index.html",
            (Alternate("fr", f"{BASE}/index.html"), Alternate("x-default", f"{BASE}/index.html")),
        )
        assert render_sitemap([rebuilt]) == render_sitemap([ONE_URL])

    def test_input_order_is_output_order(self):
        urls = [SitemapUrl(f"{BASE}/{name}.html") for name in ("zebra", "apple", "mango")]
        root = ET.fromstring(render_sitemap(urls))
        assert [e.text for e in root.findall("sm:url/sm:loc", _NS)] == [u.loc for u in urls]


# ── Hostile input ────────────────────────────────────────────────────────────


class TestEscaping:
    def test_hostile_loc_is_escaped_and_round_trips(self):
        raw = f'{BASE}/?a=1&b=2<c>"d"]]>e'
        out = render_sitemap([SitemapUrl(raw)])
        assert "&amp;" in out and "&lt;" in out and "&gt;" in out
        assert "]]>" not in out
        assert ET.fromstring(out).find("sm:url/sm:loc", _NS).text == raw

    def test_hostile_hreflang_is_escaped(self):
        """The live bug this module closes: templates/static/sitemap.xml.j2:7
        interpolated hreflang with no filter at all, under an autoescape=False
        environment, from a `locales` list nothing validated."""
        raw = 'fr" onload="x'
        out = render_sitemap([SitemapUrl(f"{BASE}/a.html", (Alternate(raw, f"{BASE}/a.html"),))])
        assert "&quot;" in out
        assert ET.fromstring(out).find("sm:url/xhtml:link", _NS).get("hreflang") == raw

    def test_attribute_whitespace_round_trips(self):
        out = render_sitemap([SitemapUrl(f"{BASE}/a.html", (Alternate("fr", f"{BASE}/a\nb\tc"),))])
        assert "&#10;" in out
        assert ET.fromstring(out).find("sm:url/xhtml:link", _NS).get("href") == f"{BASE}/a\nb\tc"

    @pytest.mark.parametrize("char", ["\x00", "\x01", "\x0c", "\x1f"], ids=lambda c: f"U+{ord(c):04X}")
    def test_control_character_in_loc_is_rejected(self, char: str):
        with pytest.raises(SitemapError, match="cannot represent"):
            render_sitemap([SitemapUrl(f"{BASE}/a{char}.html")])

    def test_control_character_in_an_attribute_is_rejected(self):
        with pytest.raises(SitemapError, match="hreflang"):
            render_sitemap([SitemapUrl(f"{BASE}/a.html", (Alternate("f\x00r", f"{BASE}/a.html"),))])
        with pytest.raises(SitemapError, match="href"):
            render_sitemap([SitemapUrl(f"{BASE}/a.html", (Alternate("fr", f"{BASE}/a\x01"),))])

    def test_lone_surrogate_is_rejected(self):
        """Otherwise it survives serialisation and fails much later, as a
        UnicodeEncodeError from _write_file naming no locale or slug."""
        with pytest.raises(SitemapError, match="U\\+D800"):
            render_sitemap([SitemapUrl(f"{BASE}/a\ud800.html")])

    def test_astral_plane_is_allowed(self):
        """The guard must not over-reject above the BMP."""
        out = render_sitemap([SitemapUrl(f"{BASE}/\U0001f600.html")])
        assert "\U0001f600" in out


# ── lastmod ──────────────────────────────────────────────────────────────────


class TestLastmod:
    def test_absent_by_default(self):
        """The reproducibility guarantee, asserted directly: an unconfigured
        build emits no clock-dependent value anywhere."""
        assert "<lastmod" not in render_sitemap([ONE_URL])

    def test_default_applies_to_every_url(self):
        urls = [SitemapUrl(f"{BASE}/{n}.html") for n in "abc"]
        root = ET.fromstring(render_sitemap(urls, default_lastmod="2026-09-14"))
        assert [e.text for e in root.findall("sm:url/sm:lastmod", _NS)] == ["2026-09-14"] * 3

    def test_per_url_overrides_the_default(self):
        urls = [SitemapUrl(f"{BASE}/a.html", lastmod="2020-01-01"), SitemapUrl(f"{BASE}/b.html")]
        root = ET.fromstring(render_sitemap(urls, default_lastmod="2026-09-14"))
        assert [e.text for e in root.findall("sm:url/sm:lastmod", _NS)] == ["2020-01-01", "2026-09-14"]

    @pytest.mark.parametrize(
        "value",
        [
            "2026-09-14",
            "2026-09-14T12:00Z",
            "2026-09-14T12:00:00Z",
            "2026-09-14T12:00:00.5Z",
            "2026-09-14T12:00:00+02:00",
            "2026-09-14T12:00:00-05:00",
        ],
    )
    def test_accepts(self, value: str):
        assert normalize_lastmod(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            # The four datetime.fromisoformat accepts and W3C does not -- the
            # reason the regex exists alongside the parse.
            "20260914",
            "2026-09-14 12:00:00Z",
            "2026-W12-1",
            "2026-09-14T12:00:00",
            # Shapes nobody means, dropped deliberately.
            "2026",
            "2026-09",
            "2026-9-4",
            # Right shape, not a day -- the reason the parse exists alongside
            # the regex.
            "2026-02-31",
            "",
            "   ",
            "yesterday",
        ],
        ids=repr,
    )
    def test_rejects(self, value: str):
        with pytest.raises(SitemapError, match="lastmod"):
            normalize_lastmod(value)

    @pytest.mark.parametrize("value", [None, 12345, ["2026-09-14"]], ids=repr)
    def test_rejects_non_strings(self, value):
        with pytest.raises(SitemapError, match="lastmod"):
            normalize_lastmod(value)


# ── Protocol limits ──────────────────────────────────────────────────────────


class TestLimits:
    def test_within_limits_is_silent(self):
        assert limit_warnings(MAX_SITEMAP_URLS, MAX_SITEMAP_BYTES) == []
        assert limit_warnings(MAX_SITEMAP_URLS - 1, 1) == []

    def test_too_many_urls_warns_and_names_the_remedy(self):
        (warning,) = limit_warnings(MAX_SITEMAP_URLS + 1, 1)
        assert "50,001" in warning
        assert "sitemapindex" in warning

    def test_too_many_bytes_warns(self):
        (warning,) = limit_warnings(1, MAX_SITEMAP_BYTES + 1)
        assert "MB" in warning

    def test_both_limits_report_separately(self):
        assert len(limit_warnings(MAX_SITEMAP_URLS + 1, MAX_SITEMAP_BYTES + 1)) == 2
