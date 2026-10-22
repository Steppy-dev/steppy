"""SEO / indexing controls for the static build target.

Covers the `seo` config block: private-by-default (noindex meta + robots.txt
Disallow), public mode (sitemap.xml, robots.txt Allow + Sitemap, canonical
links, hreflang alternates, meta descriptions, Open Graph), and the
description fallback chain. All builds run in-process via BuildAssembler; no
CLI / tsx.
"""

import re
import xml.etree.ElementTree as ET
from fnmatch import fnmatch
from pathlib import Path

import pytest

from steppy.build import BuildAssembler
from steppy.build_models import BuildConfig
from steppy.sitemap import render_sitemap
from tests.test_build import _make_config, _make_scenario, _make_screenshots


BASE = "https://guides.example.com"
PUBLIC = {"mode": "public", "baseUrl": BASE}

_SM_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "xhtml": "http://www.w3.org/1999/xhtml",
}


def _static_project(
    tmp_path: Path,
    *,
    seo=None,
    guides=None,
    navbar=None,
    groups=None,
    onboarding=None,
    locales=("fr", "nl"),
    interactive=False,
) -> tuple[BuildConfig, Path]:
    if guides is None:
        guides = [
            {
                "slug": "intro",
                "title": "Intro",
                "scenario": "intro.md",
                **({"modes": ["stepbystep", "interactive"]} if interactive else {}),
            }
        ]
    for g in guides:
        # One scenario shape for every locale set, regional ones included:
        # `_make_scenario` writes fr/nl `guide-text` keys and `GuideText.get`
        # resolves `fr_BE` through `fr`. The regional tests below therefore also
        # exercise that fallback rather than papering over it with exact keys.
        _make_scenario(tmp_path / "scenarios", g["scenario"])
        _make_screenshots(tmp_path, g["slug"])
    overrides = dict(
        target="static",
        locales=list(locales),
        defaultLocale=locales[0],
        guides=guides,
        navbar=navbar or {"order": [guides[0]["slug"]]},
    )
    if seo is not None:
        overrides["seo"] = seo
    if groups is not None:
        overrides["groups"] = groups
    if onboarding is not None:
        overrides["onboarding"] = onboarding
    return _make_config(tmp_path, **overrides), tmp_path


@pytest.fixture(autouse=True)
def _no_real_pdf_rendering(monkeypatch):
    """Never launch Chromium from this suite.

    ``handbook=True`` reaches ``_render_build_pdfs`` with real jobs, so without
    this every handbook build here prints two actual PDFs -- 0.95s instead of
    0.05s, and a hidden ``playwright install chromium`` requirement in a suite
    that is otherwise pure in-process HTML/XML assertions.

    Same shape as ``tests/test_build_fixture.py::_patch_render_pdfs``. Patching
    the *module attribute* is what works: ``_render_build_pdfs`` does
    ``from .pdf import ...`` inside the function body, so the lookup happens at
    call time.
    """
    import steppy.pdf as pdf_module

    def _fake(jobs, options=None, **kwargs):
        for job in jobs:
            job.pdf.parent.mkdir(parents=True, exist_ok=True)
            job.pdf.write_bytes(b"%PDF-1.4 fake")
        return [pdf_module.PdfResult(job=j, ok=True) for j in jobs]

    monkeypatch.setattr(pdf_module, "render_pdfs", _fake)


def _build(tmp_path: Path, handbook: bool = False, pdf: bool = False, **kwargs) -> Path:
    config, root = _static_project(tmp_path, **kwargs)
    BuildAssembler(config, root).build_all(handbook=handbook, pdf=pdf)
    return root / "output" / "generated"


def _headers_rules(gen: Path) -> dict:
    """Parse the `_headers` sidecar into {path pattern: [header lines]}."""
    rules: dict = {}
    current = None
    for line in (gen / "_headers").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            current = line.strip()
            rules[current] = []
        elif current is not None:
            rules[current].append(line.strip())
    return rules


def _noindex_patterns(gen: Path) -> list:
    return [
        pattern
        for pattern, headers in _headers_rules(gen).items()
        if any(h.lower().startswith("x-robots-tag:") and "noindex" in h.lower() for h in headers)
    ]


def _maximal(tmp_path: Path, **kwargs) -> Path:
    """Every page kind the static target can emit, in one build."""
    return _build(
        tmp_path,
        guides=[
            {"slug": "intro", "title": "Intro", "scenario": "intro.md", "modes": ["stepbystep", "interactive"]},
            {"slug": "a", "title": "A", "scenario": "a.md", "group": "module"},
        ],
        groups=[{"slug": "module", "title": "Module"}],
        navbar={"order": ["intro", "module"]},
        onboarding={"enabled": True, "guides": ["intro", "a"]},
        **kwargs,
    )


def _url_for(gen: Path, page: Path) -> str:
    """The canonical URL a rendered page should claim for itself.

    Only the *site root* maps `index.html` to a bare directory URL. Every other
    `index.html` is a meta-refresh stub rather than content, so `<group>/` is
    not a page and must never be a canonical -- that asymmetry is the one piece
    of normalisation here, and it exists because of the root, not in spite of it.
    """
    rel = page.relative_to(gen).as_posix()
    if rel == "index.html":
        return f"{BASE}/"
    return f"{BASE}/{rel}"


def _sitemap_locs(gen: Path) -> set:
    root = ET.fromstring((gen / "sitemap.xml").read_text(encoding="utf-8"))
    return {e.text for e in root.findall(".//sm:url/sm:loc", _SM_NS)}


# ── Private (default) ────────────────────────────────────────────────────────


class TestPrivateDefault:
    def test_noindex_on_every_page(self, tmp_path):
        gen = _build(tmp_path)  # no seo block → private
        pages = list(gen.rglob("*.html"))
        assert pages
        for p in pages:
            html = p.read_text(encoding="utf-8")
            assert 'name="robots" content="noindex,nofollow,noarchive"' in html, p

    def test_robots_txt_disallow(self, tmp_path):
        gen = _build(tmp_path)
        robots = (gen / "robots.txt").read_text(encoding="utf-8")
        assert "User-agent: *" in robots
        assert "Disallow: /" in robots
        assert "Sitemap:" not in robots

    def test_no_sitemap(self, tmp_path):
        gen = _build(tmp_path)
        assert not (gen / "sitemap.xml").exists()

    def test_no_canonical_or_hreflang(self, tmp_path):
        gen = _build(tmp_path)
        html = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert 'rel="canonical"' not in html
        assert "hreflang" not in html

    def test_redirect_stub_noindex(self, tmp_path):
        gen = _build(
            tmp_path,
            guides=[
                {"slug": "intro", "title": "Intro", "scenario": "intro.md"},
                {"slug": "a", "title": "A", "scenario": "a.md", "group": "module"},
            ],
            groups=[{"slug": "module", "title": "Module"}],
            navbar={"order": ["intro", "module"]},
        )
        redirect = (gen / "module" / "index.html").read_text(encoding="utf-8")
        assert "noindex,nofollow,noarchive" in redirect

    def test_interactive_page_noindex(self, tmp_path):
        gen = _build(tmp_path, interactive=True)
        inter = (gen / "intro" / "interactive.html").read_text(encoding="utf-8")
        assert "noindex,nofollow,noarchive" in inter


# ── Public ───────────────────────────────────────────────────────────────────


class TestPublicRobotsAndSitemap:
    def test_robots_txt_allow_and_sitemap(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)
        robots = (gen / "robots.txt").read_text(encoding="utf-8")
        assert "Allow: /" in robots
        assert f"Sitemap: {BASE}/sitemap.xml" in robots
        assert "Disallow: /" not in robots

    def test_no_noindex_anywhere(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)
        for p in gen.rglob("*.html"):
            assert "noindex" not in p.read_text(encoding="utf-8"), p

    def test_sitemap_wellformed_and_absolute(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)
        sm = gen / "sitemap.xml"
        assert sm.exists()
        root = ET.fromstring(sm.read_text(encoding="utf-8"))
        locs = [e.text for e in root.findall(".//sm:url/sm:loc", _SM_NS)]
        assert locs
        for loc in locs:
            assert loc.startswith(BASE + "/"), loc
        # The default-locale index is the bare site root. `index.html` still
        # exists and still serves those bytes -- it just is not the canonical
        # URL, so listing it would index the home page twice.
        assert f"{BASE}/" in locs
        assert f"{BASE}/index.html" not in locs
        assert f"{BASE}/index.nl.html" in locs
        assert f"{BASE}/intro/fr.html" in locs
        assert f"{BASE}/intro/nl.html" in locs

    def test_sitemap_has_hreflang_alternates(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)
        root = ET.fromstring((gen / "sitemap.xml").read_text(encoding="utf-8"))
        alts = root.findall(".//sm:url/xhtml:link", _SM_NS)
        assert alts
        hreflangs = {a.get("hreflang") for a in alts}
        assert {"fr", "nl", "x-default"} <= hreflangs

    def test_sitemap_excludes_redirect_stubs(self, tmp_path):
        gen = _build(
            tmp_path,
            seo=PUBLIC,
            guides=[
                {"slug": "intro", "title": "Intro", "scenario": "intro.md"},
                {"slug": "a", "title": "A", "scenario": "a.md", "group": "module"},
            ],
            groups=[{"slug": "module", "title": "Module"}],
            navbar={"order": ["intro", "module"]},
            onboarding={"enabled": True, "guides": ["intro", "a"]},
        )
        sm = (gen / "sitemap.xml").read_text(encoding="utf-8")
        assert f"{BASE}/module/fr.html" in sm
        assert f"{BASE}/getting-started/fr.html" in sm
        assert f"{BASE}/module/index.html" not in sm
        assert f"{BASE}/getting-started/index.html" not in sm
        assert f"{BASE}/intro/index.html" not in sm


class TestPublicPageHead:
    def test_guide_canonical_and_hreflang(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)
        fr = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert f'<link rel="canonical" href="{BASE}/intro/fr.html">' in fr
        assert f'<link rel="alternate" hreflang="fr" href="{BASE}/intro/fr.html">' in fr
        assert f'<link rel="alternate" hreflang="nl" href="{BASE}/intro/nl.html">' in fr
        assert f'<link rel="alternate" hreflang="x-default" href="{BASE}/intro/fr.html">' in fr

    def test_index_canonical_per_locale(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)
        idx = (gen / "index.html").read_text(encoding="utf-8")
        assert f'<link rel="canonical" href="{BASE}/">' in idx
        nl = (gen / "index.nl.html").read_text(encoding="utf-8")
        # Deliberately asymmetric, and not a bug to "fix": only the default
        # locale has a bare-root URL to be canonical at. Every other locale
        # lives at a real filename.
        assert f'<link rel="canonical" href="{BASE}/index.nl.html">' in nl

    def test_index_hreflang_points_at_the_bare_root(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)
        idx = (gen / "index.html").read_text(encoding="utf-8")
        assert f'<link rel="alternate" hreflang="fr" href="{BASE}/">' in idx
        assert f'<link rel="alternate" hreflang="x-default" href="{BASE}/">' in idx
        assert f'<link rel="alternate" hreflang="nl" href="{BASE}/index.nl.html">' in idx

    def test_single_locale_has_canonical_but_no_hreflang(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC, locales=("fr",))
        fr = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert f'<link rel="canonical" href="{BASE}/intro/fr.html">' in fr
        assert "hreflang" not in fr

    def test_group_and_onboarding_canonical(self, tmp_path):
        gen = _build(
            tmp_path,
            seo=PUBLIC,
            guides=[
                {"slug": "intro", "title": "Intro", "scenario": "intro.md"},
                {"slug": "a", "title": "A", "scenario": "a.md", "group": "module"},
            ],
            groups=[{"slug": "module", "title": "Module", "description": {"fr": "Deux guides", "nl": "Twee gidsen"}}],
            navbar={"order": ["intro", "module"]},
            onboarding={"enabled": True, "guides": ["intro", "a"]},
        )
        grp = (gen / "module" / "fr.html").read_text(encoding="utf-8")
        assert f'<link rel="canonical" href="{BASE}/module/fr.html">' in grp
        assert '<meta name="description" content="Deux guides">' in grp
        onb = (gen / "getting-started" / "fr.html").read_text(encoding="utf-8")
        assert f'<link rel="canonical" href="{BASE}/getting-started/fr.html">' in onb


class TestMetaDescription:
    def test_from_guide_description(self, tmp_path):
        gen = _build(
            tmp_path,
            seo=PUBLIC,
            guides=[
                {
                    "slug": "intro",
                    "title": "Onboarding",
                    "scenario": "intro.md",
                    "description": {"fr": "Prise en main", "nl": "Aan de slag"},
                }
            ],
        )
        fr = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert '<meta name="description" content="Prise en main">' in fr
        nl = (gen / "intro" / "nl.html").read_text(encoding="utf-8")
        assert '<meta name="description" content="Aan de slag">' in nl

    def test_falls_back_to_title(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)  # guide has no description
        fr = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert '<meta name="description" content="Intro">' in fr

    def test_index_from_seo_description(self, tmp_path):
        gen = _build(
            tmp_path,
            seo={
                "mode": "public",
                "baseUrl": BASE,
                "description": {"fr": "Tous les guides", "nl": "Alle gidsen"},
            },
        )
        idx = (gen / "index.html").read_text(encoding="utf-8")
        assert '<meta name="description" content="Tous les guides">' in idx

    def test_index_falls_back_to_site_title(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)  # no seo.description
        idx = (gen / "index.html").read_text(encoding="utf-8")
        assert '<meta name="description" content="Test Guides">' in idx


class TestOpenGraph:
    def test_og_off_by_default(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)  # openGraph not set
        fr = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert "og:" not in fr

    def test_og_on_guide_page(self, tmp_path):
        gen = _build(tmp_path, seo={"mode": "public", "baseUrl": BASE, "openGraph": True})
        fr = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert '<meta property="og:type" content="article">' in fr
        assert '<meta property="og:title" content="Intro">' in fr
        assert f'<meta property="og:url" content="{BASE}/intro/fr.html">' in fr
        # og:image is the guide's first screenshot, absolute.
        assert f'property="og:image" content="{BASE}/assets/intro/step-' in fr
        assert '<meta name="twitter:card" content="summary_large_image">' in fr

    def test_og_site_name_from_site_title(self, tmp_path):
        gen = _build(tmp_path, seo={"mode": "public", "baseUrl": BASE, "openGraph": True})
        fr = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert '<meta property="og:site_name" content="Test Guides">' in fr


class TestInteractivePublic:
    def test_interactive_canonicals_to_the_stepbystep_page(self, tmp_path):
        """Same steps, different shell. A self-canonical here is what had every
        guide occupying three or four near-identical indexable URLs."""
        gen = _build(tmp_path, seo=PUBLIC, interactive=True)
        inter = (gen / "intro" / "interactive.html").read_text(encoding="utf-8")
        assert f'<link rel="canonical" href="{BASE}/intro/fr.html">' in inter
        # A page that canonicals elsewhere must carry no alternates, and this
        # one serves every locale from a single URL anyway.
        assert "hreflang" not in inter

    def test_interactive_not_in_sitemap(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC, interactive=True)
        sm = (gen / "sitemap.xml").read_text(encoding="utf-8")
        assert f"{BASE}/intro/interactive.html" not in sm
        assert f"{BASE}/intro/fr.html" in sm


# ── The invariant ────────────────────────────────────────────────────────────


class TestSitemapInvariant:
    """The sitemap lists exactly the pages that are indexable and their own
    canonical -- asserted against the emitted HTML, with no page-type special
    cases, so a new kind of page cannot quietly opt itself in or out."""

    def test_sitemap_is_exactly_the_self_canonical_pages(self, tmp_path):
        gen = _maximal(tmp_path, seo=PUBLIC, handbook=True)
        expected = set()
        for page in sorted(gen.rglob("*.html")):
            html = page.read_text(encoding="utf-8")
            if "noindex" in html:
                continue
            match = re.search(r'<link rel="canonical" href="([^"]+)"', html)
            # The assertion doing the real work: it is what catches the handbook
            # the day someone drops the hardcoded noindex in guide.book.html.j2
            # without wiring in _seo_head.html.j2.
            assert match, f"{page} is indexable but declares no canonical"
            if match.group(1) == _url_for(gen, page):
                expected.add(match.group(1))
        assert expected
        assert _sitemap_locs(gen) == expected

    def test_every_page_declares_a_lang(self, tmp_path):
        """Mirrors test_noindex_on_every_page's all-pages walk. `<html lang>` is
        an accessibility attribute, so it must survive in private mode too --
        where the seo context is a bare {"robots": ...} dict."""
        for seo in (None, PUBLIC):
            gen = _maximal(tmp_path / f"seo-{bool(seo)}", seo=seo, handbook=True)
            for page in gen.rglob("*.html"):
                html = page.read_text(encoding="utf-8")
                match = re.search(r"<html lang=\"([^\"]*)\"", html)
                assert match, f"{page} has no <html lang>"
                assert match.group(1), f"{page} has an empty <html lang>"


# ── Redirect stubs ───────────────────────────────────────────────────────────


class TestRedirectStubs:
    def test_guides_get_one_too(self, tmp_path):
        """Groups, onboarding and the handbook always had a stub; a bare
        `/<slug>/` used to 404."""
        gen = _build(tmp_path, seo=PUBLIC)
        stub = (gen / "intro" / "index.html").read_text(encoding="utf-8")
        assert 'http-equiv="refresh"' in stub
        assert f'<link rel="canonical" href="{BASE}/intro/fr.html">' in stub
        assert f"{BASE}/intro/index.html" not in (gen / "sitemap.xml").read_text(encoding="utf-8")

    def test_canonical_is_absolute_in_public_mode(self, tmp_path):
        gen = _maximal(tmp_path, seo=PUBLIC)
        for directory in ("intro", "module", "getting-started"):
            stub = (gen / directory / "index.html").read_text(encoding="utf-8")
            assert f'<link rel="canonical" href="{BASE}/{directory}/fr.html">' in stub

    def test_public_stubs_are_not_noindexed(self, tmp_path):
        """A noindex page canonicalling into an indexable cluster is a known
        footgun, and a zero-delay meta refresh already reads as a redirect."""
        gen = _maximal(tmp_path, seo=PUBLIC)
        assert "noindex" not in (gen / "module" / "index.html").read_text(encoding="utf-8")

    def test_private_stubs_are_noindexed(self, tmp_path):
        gen = _maximal(tmp_path)
        for directory in ("intro", "module", "getting-started"):
            assert "noindex" in (gen / directory / "index.html").read_text(encoding="utf-8")


# ── Handbook ─────────────────────────────────────────────────────────────────


class TestHandbook:
    def test_noindex_and_absent_from_the_sitemap(self, tmp_path):
        """Deliberate exception: one document duplicating every guide on the
        site. Out of the index in both SEO modes."""
        gen = _build(tmp_path, seo=PUBLIC, handbook=True)
        assert "noindex" in (gen / "handbook" / "fr.html").read_text(encoding="utf-8")
        assert "handbook" not in (gen / "sitemap.xml").read_text(encoding="utf-8")


# ── Locales: the two-form contract ───────────────────────────────────────────


class TestLocaleNormalisation:
    def test_authored_tag_names_the_file_and_the_normalised_tag_the_language(self, tmp_path):
        """One test pinning the whole split: the authored spelling is a join key
        with capture directories and the host app's i18n code, so it stays
        verbatim in URLs; only hreflang and <html lang> get canonical BCP-47."""
        gen = _build(tmp_path, seo=PUBLIC, locales=("fr_BE", "nl-be"))
        assert (gen / "intro" / "fr_BE.html").exists()
        assert (gen / "intro" / "nl-be.html").exists()

        fr = (gen / "intro" / "fr_BE.html").read_text(encoding="utf-8")
        assert '<html lang="fr-BE">' in fr
        assert f'<link rel="canonical" href="{BASE}/intro/fr_BE.html">' in fr
        assert f'<link rel="alternate" hreflang="fr-BE" href="{BASE}/intro/fr_BE.html">' in fr
        assert f'<link rel="alternate" hreflang="nl-BE" href="{BASE}/intro/nl-be.html">' in fr

        root = ET.fromstring((gen / "sitemap.xml").read_text(encoding="utf-8"))
        hreflangs = {a.get("hreflang") for a in root.findall(".//sm:url/xhtml:link", _SM_NS)}
        assert {"fr-BE", "nl-BE", "x-default"} <= hreflangs

    def test_localized_text_falls_back_to_the_language_subtag(self, tmp_path):
        """Without this rung an nl-BE page would carry the French description
        while declaring lang="nl-BE" -- two URLs, identical text, different
        declared languages, which is what makes a crawler drop the cluster."""
        gen = _build(
            tmp_path,
            seo=PUBLIC,
            locales=("fr_BE", "nl-be"),
            guides=[
                {
                    "slug": "intro",
                    "title": "Intro",
                    "scenario": "intro.md",
                    "description": {"fr": "Prise en main", "nl": "Aan de slag"},
                }
            ],
        )
        fr = (gen / "intro" / "fr_BE.html").read_text(encoding="utf-8")
        assert '<meta name="description" content="Prise en main">' in fr
        assert '<meta name="description" content="Aan de slag">' in (gen / "intro" / "nl-be.html").read_text(
            encoding="utf-8"
        )


class TestOgLocale:
    def test_emitted_for_a_regional_tag(self, tmp_path):
        gen = _build(tmp_path, seo={**PUBLIC, "openGraph": True}, locales=("fr_BE", "nl-be"))
        assert '<meta property="og:locale" content="fr_BE">' in (gen / "intro" / "fr_BE.html").read_text(
            encoding="utf-8"
        )

    def test_omitted_for_a_region_less_tag(self, tmp_path):
        """Open Graph is language_TERRITORY: a bare "fr" was never a valid value,
        and inventing fr_FR would be a guess this project cannot make."""
        gen = _build(tmp_path, seo={**PUBLIC, "openGraph": True})
        fr = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert "og:title" in fr
        assert "og:locale" not in fr


# ── lastmod ──────────────────────────────────────────────────────────────────


class TestLastmod:
    def test_absent_by_default(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)
        assert "<lastmod" not in (gen / "sitemap.xml").read_text(encoding="utf-8")

    def test_applied_to_every_url_when_configured(self, tmp_path):
        gen = _build(tmp_path, seo={**PUBLIC, "lastmod": "2026-09-14"})
        root = ET.fromstring((gen / "sitemap.xml").read_text(encoding="utf-8"))
        urls = root.findall(".//sm:url", _SM_NS)
        assert urls
        assert all(u.find("sm:lastmod", _SM_NS).text == "2026-09-14" for u in urls)


# ── Template override ────────────────────────────────────────────────────────


class TestSitemapTemplateOverride:
    def test_a_project_template_still_wins(self, tmp_path):
        """Steppy bundles no sitemap template any more, but a project that needs
        <image:image>, <video:video> or a <sitemapindex> still overrides the name
        -- silently ignoring a well-formed override is the failure _templating.py
        exists to prevent."""
        config, root = _static_project(tmp_path, seo=PUBLIC)
        override = root / "templates" / "static"
        override.mkdir(parents=True)
        (override / "sitemap.xml.j2").write_text(
            "<!-- STEPPY-OVERRIDE -->\n{% for url in urls %}{{ url.loc }}\n{% endfor %}",
            encoding="utf-8",
        )
        BuildAssembler(config, root).build_all()
        sitemap = (root / "output" / "generated" / "sitemap.xml").read_text(encoding="utf-8")
        assert "STEPPY-OVERRIDE" in sitemap
        assert f"{BASE}/intro/fr.html" in sitemap

    def test_the_generator_is_the_default(self, tmp_path):
        """No project template, no bundled one: build.py and the leaf agree."""
        config, root = _static_project(tmp_path, seo=PUBLIC)
        assembler = BuildAssembler(config, root)
        assembler.build_all()
        written = (root / "output" / "generated" / "sitemap.xml").read_text(encoding="utf-8")
        assert written == render_sitemap(assembler._sitemap_urls())


# ── The premise the canonical rule rests on ──────────────────────────────────


class TestStepbystepIsAlwaysEmitted:
    def test_an_interactive_only_guide_still_gets_per_locale_pages(self, tmp_path):
        """`modes` selects which *extra* siblings a guide gets; the step-by-step
        pages are emitted unconditionally (`_build_guide` has no
        `GuideMode.STEPBYSTEP` check, and the enum's docstring says so). That is
        why `_guide_primary_site_rel` has no "unless there is no step-by-step
        page" branch.

        If anyone ever adds that guard to `_build_guide`, this test fails first
        and says so -- at which point `_sitemap_urls` has to change too, because
        it would otherwise advertise `<slug>/<locale>.html` for files that no
        longer exist.
        """
        gen = _build(
            tmp_path,
            seo=PUBLIC,
            guides=[{"slug": "intro", "title": "Intro", "scenario": "intro.md", "modes": ["interactive"]}],
        )
        fr = gen / "intro" / "fr.html"
        assert fr.exists(), "stepbystep pages are emitted for every guide, whatever `modes` says"
        assert f'<link rel="canonical" href="{BASE}/intro/fr.html">' in fr.read_text(encoding="utf-8")
        assert f"{BASE}/intro/fr.html" in _sitemap_locs(gen)
        inter = (gen / "intro" / "interactive.html").read_text(encoding="utf-8")
        assert f'<link rel="canonical" href="{BASE}/intro/fr.html">' in inter


class TestVideoPagePublic:
    def test_video_canonicals_to_stepbystep_and_stays_out_of_the_sitemap(self, tmp_path, monkeypatch):
        import steppy.video as video_module

        def _fake_render(batch, options=None, **kwargs):
            results = []
            for job in batch:
                job.out.parent.mkdir(parents=True, exist_ok=True)
                job.out.write_bytes(b"MP4")
                job.vtt.write_text("WEBVTT\n", encoding="utf-8")
                job.poster.write_bytes(b"JPEG")
                results.append(video_module.VideoResult(job=job, ok=True))
            return results

        monkeypatch.setattr(video_module, "render_videos", _fake_render)
        monkeypatch.setattr(video_module, "find_ffmpeg", lambda options: "/usr/bin/ffmpeg")

        config, root = _static_project(
            tmp_path,
            seo=PUBLIC,
            guides=[
                {
                    "slug": "intro",
                    "title": "Intro",
                    "scenario": "intro.md",
                    "modes": ["stepbystep", "interactive", "video"],
                }
            ],
        )
        BuildAssembler(config, root).build_all(video=True)
        gen = root / "output" / "generated"

        video_page = gen / "intro" / "video.html"
        assert video_page.exists()
        assert f'<link rel="canonical" href="{BASE}/intro/fr.html">' in video_page.read_text(encoding="utf-8")
        locs = _sitemap_locs(gen)
        assert f"{BASE}/intro/video.html" not in locs
        assert f"{BASE}/intro/interactive.html" not in locs
        assert f"{BASE}/intro/fr.html" in locs


# ── The _headers sidecar ─────────────────────────────────────────────────────


class TestHeadersSidecar:
    """A PDF cannot carry a robots meta, so an HTTP header is the only control.

    Read by Netlify and Cloudflare Pages; ignored by GitHub and GitLab Pages,
    which is stated in the docs rather than implied away.
    """

    def test_public_noindexes_pdfs(self, tmp_path):
        gen = _build(tmp_path, seo=PUBLIC)
        assert _noindex_patterns(gen) == ["/*.pdf"]

    def test_private_noindexes_everything(self, tmp_path):
        """Private mode's per-page noindex cannot reach a PDF, and a robots.txt
        Disallow stops the fetch without stopping the listing."""
        gen = _build(tmp_path)
        assert _noindex_patterns(gen) == ["/*"]

    def test_every_emitted_pdf_is_covered(self, tmp_path):
        """The walk TestSitemapInvariant structurally cannot do: it globs
        *.html, so a PDF is invisible to it -- which is exactly how a noindex
        handbook came to have a fully indexable published copy."""
        gen = _build(tmp_path, seo=PUBLIC, handbook=True, pdf=True)
        pdfs = sorted(gen.rglob("*.pdf"))
        assert pdfs, "no PDFs emitted -- this guard would pass vacuously"
        patterns = _noindex_patterns(gen)
        for pdf in pdfs:
            rel = "/" + pdf.relative_to(gen).as_posix()
            assert any(fnmatch(rel, pattern) for pattern in patterns), rel

    def test_the_handbook_pdf_is_the_case_that_motivated_it(self, tmp_path):
        """Its HTML is hardcoded noindex; without the header its PDF was the
        only indexable form of the same content."""
        gen = _build(tmp_path, seo=PUBLIC, handbook=True)
        assert (gen / "handbook" / "fr.pdf").exists()
        assert "noindex" in (gen / "handbook" / "fr.html").read_text(encoding="utf-8")
        assert any(fnmatch("/handbook/fr.pdf", p) for p in _noindex_patterns(gen))


class TestBaseUrlPathWarning:
    """robots.txt and _headers are honoured only at the origin root, but a
    GitHub Pages *project* site lives under /<repo>/ -- so a correct baseUrl
    there silently disarms both."""

    def test_warns_when_base_url_has_a_path(self, tmp_path, capsys):
        _build(tmp_path, seo={"mode": "public", "baseUrl": "https://owner.github.io/repo"})
        err = capsys.readouterr().err
        assert "robots.txt and _headers" in err
        assert "/repo/" in err

    def test_silent_at_an_origin_root(self, tmp_path, capsys):
        _build(tmp_path, seo=PUBLIC)
        assert "_headers" not in capsys.readouterr().err

    def test_canonical_links_are_unaffected(self, tmp_path):
        """The warning is about two sidecars, not about the URLs -- those are
        absolute and correct under a path prefix."""
        gen = _build(tmp_path, seo={"mode": "public", "baseUrl": "https://owner.github.io/repo"})
        fr = (gen / "intro" / "fr.html").read_text(encoding="utf-8")
        assert '<link rel="canonical" href="https://owner.github.io/repo/intro/fr.html">' in fr
