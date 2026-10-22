"""Integration test using the fixture project structure."""

import json
import struct
import zlib
from pathlib import Path

import pytest

from steppy.build import BuildAssembler
from steppy.build_models import BuildConfig


FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "demo-guide"


def _ensure_screenshots():
    """Create dummy screenshots if they don't exist."""

    def _minimal_png():
        sig = b"\x89PNG\r\n\x1a\n"

        def chunk(ctype, data):
            c = ctype + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        raw = zlib.compress(b"\x00\xff\xff\xff")
        idat = chunk(b"IDAT", raw)
        iend = chunk(b"IEND", b"")
        return sig + ihdr + idat + iend

    sidecar = {
        "viewport": {"width": 1280, "height": 720},
        "target": {"kind": "single", "bbox": {"x": 100, "y": 200, "width": 50, "height": 30}},
    }
    png = _minimal_png()

    for slug in ["onboarding", "invoices"]:
        d = FIXTURE_ROOT / "output" / "screenshots" / slug
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, 3):
            png_path = d / f"step-{i:02d}.png"
            if not png_path.exists():
                png_path.write_bytes(png)
            json_path = d / f"step-{i:02d}.json"
            if not json_path.exists():
                json_path.write_text(json.dumps(sidecar))


@pytest.fixture
def fixture_config(tmp_path):
    """Create a BuildConfig using fixture scenarios but writing to tmp_path."""
    _ensure_screenshots()
    return BuildConfig(
        scenariosRoot=str(FIXTURE_ROOT / "e2e" / "scenarios"),
        screenshotsDir=str(FIXTURE_ROOT / "output" / "screenshots"),
        outputDir=str(tmp_path / "generated"),
        assetsDir=str(tmp_path / "assets"),
        role="editor",
        locales=["fr", "nl"],
        defaultLocale="fr",
        # These tests assert Angular output; the production default is now static.
        target="angular",
        site={"title": "Demo Guides"},
        guides=[
            {"slug": "onboarding", "title": "Onboarding", "scenario": "editor/onboarding.md"},
            {
                "slug": "invoices",
                "title": "Invoices",
                "scenario": "editor/invoices.md",
                "modes": ["stepbystep", "interactive"],
            },
        ],
        navbar={"order": ["onboarding", "invoices"]},
    )


class TestFixtureBuild:
    def test_full_build_generates_all_files(self, fixture_config, tmp_path):
        assembler = BuildAssembler(fixture_config, tmp_path)
        assembler.build_all()

        gen = tmp_path / "generated"
        assert (gen / "editor.routes.ts").exists()
        assert (gen / "editor.nav.json").exists()
        assert (gen / "i18n" / "editor" / "fr.json").exists()
        assert (gen / "i18n" / "editor" / "nl.json").exists()
        assert (gen / ".gitignore").exists()

        # Onboarding
        assert (gen / "onboarding" / "onboarding.component.ts").exists()
        assert (gen / "onboarding" / "onboarding.content.ts").exists()
        assert (gen / "onboarding" / "onboarding-stepbystep-fr.html").exists()
        assert (gen / "onboarding" / "onboarding-stepbystep-nl.html").exists()

        # Invoices (with interactive mode)
        assert (gen / "invoices" / "invoices.component.ts").exists()
        assert (gen / "invoices" / "invoices.content.ts").exists()
        assert (gen / "invoices" / "invoices-interactive.component.ts").exists()
        assert (gen / "invoices" / "invoices-stepbystep-fr.html").exists()
        assert (gen / "invoices" / "invoices-stepbystep-nl.html").exists()

        # Dropped artifacts (now host-app concerns)
        for dropped in (
            "index.ts",
            "guides.routes.ts",
            "guides.navbar.ts",
            "guides.manifest.ts",
            "locale.ts",
            "layout.ts",
        ):
            assert not (gen / dropped).exists(), f"Should not exist: {dropped}"

    def test_routes_contain_both_guides(self, fixture_config, tmp_path):
        assembler = BuildAssembler(fixture_config, tmp_path)
        assembler.build_all()

        routes = (tmp_path / "generated" / "editor.routes.ts").read_text(encoding="utf-8")
        assert "'onboarding'" in routes
        assert "'invoices'" in routes
        assert "OnboardingComponent" in routes
        assert "InvoicesComponent" in routes
        assert "EDITOR_ROUTES" in routes

    def test_i18n_has_site_title(self, fixture_config, tmp_path):
        assembler = BuildAssembler(fixture_config, tmp_path)
        assembler.build_all()

        bundle = json.loads((tmp_path / "generated" / "i18n" / "editor" / "fr.json").read_text(encoding="utf-8"))
        assert bundle["EDITOR"]["site"]["title"] == "Demo Guides"

    def test_content_has_both_locales(self, fixture_config, tmp_path):
        assembler = BuildAssembler(fixture_config, tmp_path)
        assembler.build_all()

        content = (tmp_path / "generated" / "onboarding" / "onboarding.content.ts").read_text(encoding="utf-8")
        assert "'fr':" in content
        assert "'nl':" in content

    def test_screenshots_copied(self, fixture_config, tmp_path):
        assembler = BuildAssembler(fixture_config, tmp_path)
        assembler.build_all()

        assets = tmp_path / "assets"
        assert (assets / "onboarding" / "step-01.png").exists()
        assert (assets / "invoices" / "step-01.png").exists()

    def test_interactive_component_mounts_steppy_guide(self, fixture_config, tmp_path):
        """Stage 4: the interactive component is a real ``<steppy-guide>``
        host bridged to ngx-translate's current locale."""
        assembler = BuildAssembler(fixture_config, tmp_path)
        assembler.build_all()

        interactive = (tmp_path / "generated" / "invoices" / "invoices-interactive.component.ts").read_text(
            encoding="utf-8"
        )
        assert "Coming soon" not in interactive
        assert "InvoicesInteractiveComponent" in interactive
        assert "<steppy-guide" in interactive
        assert "TranslateService" in interactive

    def test_invoices_has_mode_switcher(self, fixture_config, tmp_path):
        assembler = BuildAssembler(fixture_config, tmp_path)
        assembler.build_all()

        component = (tmp_path / "generated" / "invoices" / "invoices.component.ts").read_text(encoding="utf-8")
        assert "modeOptions" in component
        assert "SelectButtonModule" in component

    def test_onboarding_no_mode_switcher(self, fixture_config, tmp_path):
        assembler = BuildAssembler(fixture_config, tmp_path)
        assembler.build_all()

        component = (tmp_path / "generated" / "onboarding" / "onboarding.component.ts").read_text(encoding="utf-8")
        assert "modeOptions" not in component


# ── Static target end-to-end against the fixture ─────────────────────────────


@pytest.fixture
def static_fixture_config(tmp_path):
    """Same fixture scenarios but built as a self-contained static site.
    Drops 'interactive' mode from invoices (incompatible with static)."""
    _ensure_screenshots()
    return BuildConfig(
        scenariosRoot=str(FIXTURE_ROOT / "e2e" / "scenarios"),
        screenshotsDir=str(FIXTURE_ROOT / "output" / "screenshots"),
        outputDir=str(tmp_path / "site"),
        assetsDir=str(tmp_path / "unused"),  # ignored by static target
        role="editor",
        locales=["fr", "nl"],
        defaultLocale="fr",
        target="static",
        site={"title": "Demo Guides"},
        guides=[
            {"slug": "onboarding", "title": "Onboarding", "scenario": "editor/onboarding.md"},
            {"slug": "invoices", "title": "Invoices", "scenario": "editor/invoices.md"},
        ],
        navbar={"order": ["onboarding", "invoices"]},
    )


class TestFixtureStaticBuild:
    def test_full_static_build_emits_self_contained_site(self, static_fixture_config, tmp_path):
        BuildAssembler(static_fixture_config, tmp_path).build_all()

        site = tmp_path / "site"
        # Site files
        assert (site / "index.html").exists()
        assert (site / "manifest.json").exists()
        assert (site / "assets" / "guides.css").exists()
        assert (site / ".nojekyll").exists()
        # Per-guide pages
        for slug in ("onboarding", "invoices"):
            for loc in ("fr", "nl"):
                assert (site / slug / f"{loc}.html").exists()
        # Screenshots co-located under site/assets/
        assert (site / "assets" / "onboarding" / "step-01.png").exists()
        assert (site / "assets" / "invoices" / "step-01.png").exists()
        # Configured assetsDir is NOT used
        assert not (tmp_path / "unused").exists()

    def test_manifest_pages_all_resolve(self, static_fixture_config, tmp_path):
        BuildAssembler(static_fixture_config, tmp_path).build_all()

        site = tmp_path / "site"
        manifest = json.loads((site / "manifest.json").read_text(encoding="utf-8"))
        for guide in manifest["guides"]:
            for loc, rel_path in guide["pages"].items():
                assert (site / rel_path).exists(), f"Missing page {rel_path} for {guide['slug']}/{loc}"

    def test_page_screenshot_paths_resolve(self, static_fixture_config, tmp_path):
        """Every <img src> in every page must resolve to a real file under site/."""
        import re

        BuildAssembler(static_fixture_config, tmp_path).build_all()

        site = tmp_path / "site"
        img_re = re.compile(r'<img[^>]*src="([^"]+)"')
        for page_path in site.rglob("*.html"):
            html = page_path.read_text(encoding="utf-8")
            for src in img_re.findall(html):
                if src.startswith(("http://", "https://", "data:")):
                    continue
                resolved = (page_path.parent / src).resolve()
                assert resolved.exists(), f"Broken <img src='{src}'> in {page_path}"

    def test_static_build_accepts_interactive(self, tmp_path):
        """Stage 4 lifts the historical rejection — both targets now render
        interactive guides via the same Lit ``<steppy-guide>`` web component.
        This test confirms the BuildConfig validates without raising."""
        cfg = BuildConfig(
            scenariosRoot=str(FIXTURE_ROOT / "e2e" / "scenarios"),
            screenshotsDir=str(FIXTURE_ROOT / "output" / "screenshots"),
            outputDir=str(tmp_path / "site"),
            assetsDir=str(tmp_path / "unused"),
            role="editor",
            locales=["fr"],
            target="static",
            site={"title": "X"},
            guides=[
                {
                    "slug": "invoices",
                    "title": "Invoices",
                    "scenario": "editor/invoices.md",
                    "modes": ["stepbystep", "interactive"],
                },
            ],
            navbar={"order": ["invoices"]},
        )
        # No raise = success. Sanity-check the parsed shape.
        assert cfg.target.value == "static"
        from steppy.build_models import GuideMode

        assert GuideMode.INTERACTIVE in cfg.guides[0].modes


def _make_layout_screenshot_tree(dest_root: Path, slug: str, locales: list[str], layouts: list[str]):
    """Create a fresh ``<dest_root>/<slug>/<locale>/<layout>/`` tree under
    ``dest_root`` (a tmp dir), seeded from the canonical fixture screenshots.
    Keeps the shared fixture directory untouched between tests."""
    _ensure_screenshots()
    src_dir = FIXTURE_ROOT / "output" / "screenshots" / slug
    for locale in locales:
        for layout in layouts:
            target_dir = dest_root / slug / locale / layout
            target_dir.mkdir(parents=True, exist_ok=True)
            for png in src_dir.glob("step-*.png"):
                (target_dir / png.name).write_bytes(png.read_bytes())
            for js in src_dir.glob("step-*.json"):
                (target_dir / js.name).write_text(js.read_text(encoding="utf-8"), encoding="utf-8")


@pytest.fixture
def layouts_fixture_config(tmp_path):
    """Build config with `layouts: [desktop, mobile]` plus all the per-layout
    screenshots needed to satisfy the assembler. Uses the fixture's existing
    onboarding scenario (which has no `layouts:` filter on its steps, so every
    step appears in both layouts). Screenshots are seeded into a tmp dir so
    we don't mutate the shared fixture."""
    screenshots_dir = tmp_path / "screenshots"
    _make_layout_screenshot_tree(screenshots_dir, "onboarding", ["fr", "nl"], ["desktop", "mobile"])
    return BuildConfig(
        scenariosRoot=str(FIXTURE_ROOT / "e2e" / "scenarios"),
        screenshotsDir=str(screenshots_dir),
        outputDir=str(tmp_path / "generated"),
        assetsDir=str(tmp_path / "assets"),
        role="editor",
        locales=["fr", "nl"],
        defaultLocale="fr",
        layouts=["desktop", "mobile"],
        defaultLayout="desktop",
        # TestFixtureLayoutsAngular asserts Angular output; the default is now static.
        target="angular",
        site={"title": "Demo Guides"},
        guides=[
            {"slug": "onboarding", "title": "Onboarding", "scenario": "editor/onboarding.md"},
        ],
        navbar={"order": ["onboarding"]},
    )


class TestFixtureLayoutsAngular:
    def test_no_layout_ts_emitted(self, layouts_fixture_config, tmp_path):
        """layout.ts is dropped entirely — components manage their own
        layout signal locally."""
        BuildAssembler(layouts_fixture_config, tmp_path).build_all()
        assert not (tmp_path / "generated" / "layout.ts").exists()

    def test_content_ts_has_nested_locale_layout_shape(self, layouts_fixture_config, tmp_path):
        BuildAssembler(layouts_fixture_config, tmp_path).build_all()
        content = (tmp_path / "generated" / "onboarding" / "onboarding.content.ts").read_text(encoding="utf-8")
        # Outer keys are locales, inner keys are layout names
        assert "Record<string, Record<string, string>>" in content
        assert "'fr':" in content
        assert "'nl':" in content
        assert "'desktop':" in content
        assert "'mobile':" in content

    def test_component_uses_internal_layout_signal(self, layouts_fixture_config, tmp_path):
        BuildAssembler(layouts_fixture_config, tmp_path).build_all()
        component = (tmp_path / "generated" / "onboarding" / "onboarding.component.ts").read_text(encoding="utf-8")
        # No service import — layout is a local signal
        assert "GuidesLayoutService" not in component
        assert "selectedLayout" in component
        assert "signal<string>('desktop')" in component
        assert "layoutOptions" in component
        # The trustedHtml must select by both locale and layout
        assert "byLayout[layoutKey]" in component

    def test_screenshots_copied_to_per_layout_dirs(self, layouts_fixture_config, tmp_path):
        BuildAssembler(layouts_fixture_config, tmp_path).build_all()
        assets = tmp_path / "assets"
        for locale in ("fr", "nl"):
            for layout in ("desktop", "mobile"):
                assert (assets / "onboarding" / locale / layout / "step-01.png").exists(), (
                    f"Missing screenshot for {locale}/{layout}"
                )


@pytest.fixture
def layouts_static_fixture_config(tmp_path):
    """Static target with layouts configured."""
    screenshots_dir = tmp_path / "screenshots"
    _make_layout_screenshot_tree(screenshots_dir, "onboarding", ["fr", "nl"], ["desktop", "mobile"])
    return BuildConfig(
        scenariosRoot=str(FIXTURE_ROOT / "e2e" / "scenarios"),
        screenshotsDir=str(screenshots_dir),
        outputDir=str(tmp_path / "site"),
        assetsDir=str(tmp_path / "unused"),
        role="editor",
        locales=["fr", "nl"],
        defaultLocale="fr",
        layouts=["desktop", "mobile"],
        defaultLayout="desktop",
        target="static",
        site={"title": "Demo Guides"},
        guides=[
            {"slug": "onboarding", "title": "Onboarding", "scenario": "editor/onboarding.md"},
        ],
        navbar={"order": ["onboarding"]},
    )


class TestFixtureLayoutsStatic:
    def test_page_embeds_both_layouts_with_toggle(self, layouts_static_fixture_config, tmp_path):
        BuildAssembler(layouts_static_fixture_config, tmp_path).build_all()
        page = (tmp_path / "site" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        # Both layouts are embedded in data-layout wrappers
        assert 'data-layout="desktop"' in page
        assert 'data-layout="mobile"' in page
        # Default layout is marked active in HTML
        assert 'class="steppy-layout steppy-layout-active" data-layout="desktop"' in page
        # Layout switcher is rendered
        assert "steppy-layout-switch" in page
        # Toggle JS bundles the per-layout step lists
        assert "__STEPPY_STEPS_BY_LAYOUT__" in page

    def test_static_screenshots_per_layout(self, layouts_static_fixture_config, tmp_path):
        BuildAssembler(layouts_static_fixture_config, tmp_path).build_all()
        assets = tmp_path / "site" / "assets"
        for locale in ("fr", "nl"):
            for layout in ("desktop", "mobile"):
                assert (assets / "onboarding" / locale / layout / "step-01.png").exists()

    def test_manifest_includes_layouts(self, layouts_static_fixture_config, tmp_path):
        BuildAssembler(layouts_static_fixture_config, tmp_path).build_all()
        manifest = json.loads((tmp_path / "site" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["layouts"] == ["desktop", "mobile"]
        assert manifest["defaultLayout"] == "desktop"

    def test_page_screenshot_paths_resolve_with_layouts(self, layouts_static_fixture_config, tmp_path):
        """Every <img src> in every page must resolve to a real file."""
        import re

        BuildAssembler(layouts_static_fixture_config, tmp_path).build_all()
        site = tmp_path / "site"
        img_re = re.compile(r'<img[^>]*src="([^"]+)"')
        for page_path in site.rglob("*.html"):
            html = page_path.read_text(encoding="utf-8")
            for src in img_re.findall(html):
                if src.startswith(("http://", "https://", "data:")):
                    continue
                resolved = (page_path.parent / src).resolve()
                assert resolved.exists(), f"Broken <img src='{src}'> in {page_path}"


class TestMobileOnlyStepEndToEnd:
    """End-to-end check that a step gated by ``layouts: [mobile]`` shows up
    in the mobile guide page but NOT the desktop one. This is the headline
    use case: mobile has an extra "tap hamburger" step that desktop doesn't."""

    def _build_with_mobile_only_step(self, tmp_path):
        sc_root = tmp_path / "scenarios" / "editor"
        sc_root.mkdir(parents=True)
        (sc_root / "mobile-flow.md").write_text(
            "---\n"
            "title: Mobile flow\n"
            "tags: [user-guide-editor]\n"
            "guide:\n  enabled: true\n"
            "---\n\n"
            "### 1. Open dashboard\n"
            "- role: editor\n"
            "- action: navigate\n"
            "- target: /\n"
            "- screenshot: editor\n"
            "- guide-text:\n    fr: Tableau de bord\n\n"
            "### 2. Tap hamburger (mobile only)\n"
            "- role: editor\n"
            "- action: click\n"
            "- selector: '[data-testid=hamburger]'\n"
            "- screenshot: editor\n"
            "- only-in: guide\n"
            "- layouts:\n  - mobile\n"
            "- guide-text:\n    fr: Ouvrez le menu\n\n"
            "### 3. Pick orders\n"
            "- role: editor\n"
            "- action: click\n"
            "- selector: '[data-testid=orders]'\n"
            "- screenshot: editor\n"
            "- guide-text:\n    fr: Cliquez sur Commandes\n",
            encoding="utf-8",
        )

        # Per-(locale, layout) screenshots. Desktop has only steps 1 and 3;
        # mobile has all three (because step 2 is mobile-only).
        ss_root = tmp_path / "screenshots" / "mobile-flow"
        for layout in ("desktop", "mobile"):
            d = ss_root / "fr" / layout
            d.mkdir(parents=True)
            steps = [1, 2, 3] if layout == "mobile" else [1, 3]
            for n in steps:
                _write_minimal_png(d / f"step-{n:02d}.png")
                _write_minimal_sidecar(d / f"step-{n:02d}.json")

        cfg = BuildConfig(
            scenariosRoot=str(tmp_path / "scenarios"),
            screenshotsDir=str(tmp_path / "screenshots"),
            outputDir=str(tmp_path / "site"),
            assetsDir=str(tmp_path / "unused"),
            role="editor",
            locales=["fr"],
            defaultLocale="fr",
            layouts=["desktop", "mobile"],
            defaultLayout="desktop",
            target="static",
            site={"title": "Mobile demo"},
            guides=[
                {"slug": "mobile-flow", "title": "Mobile flow", "scenario": "editor/mobile-flow.md"},
            ],
            navbar={"order": ["mobile-flow"]},
        )
        BuildAssembler(cfg, tmp_path).build_all()
        return tmp_path / "site" / "mobile-flow" / "fr.html"

    def test_mobile_only_step_appears_only_in_mobile_layout(self, tmp_path):
        import re

        page = self._build_with_mobile_only_step(tmp_path).read_text(encoding="utf-8")

        desktop_div = re.search(
            r'<div class="steppy-layout[^"]*" data-layout="desktop">(.+?)</div>\s*<div class="steppy-layout',
            page,
            re.DOTALL,
        )
        mobile_div = re.search(
            r'<div class="steppy-layout" data-layout="mobile">(.+?)</div>\s*</main>',
            page,
            re.DOTALL,
        )
        assert desktop_div and mobile_div

        desktop = desktop_div.group(1)
        mobile = mobile_div.group(1)

        # Desktop has steps 1 and 3, NOT 2
        assert "step-01" in desktop
        assert "step-03" in desktop
        assert "step-02" not in desktop, "Desktop must skip the mobile-only step"

        # Mobile has all three
        assert "step-01" in mobile
        assert "step-02" in mobile
        assert "step-03" in mobile

    def test_per_layout_step_lists_in_js_bundle(self, tmp_path):
        import re

        page = self._build_with_mobile_only_step(tmp_path).read_text(encoding="utf-8")

        m = re.search(r"__STEPPY_STEPS_BY_LAYOUT__\s*=\s*({.*?});", page)
        assert m, "Expected __STEPPY_STEPS_BY_LAYOUT__ JSON in page"
        data = json.loads(m.group(1))
        assert len(data["desktop"]) == 2
        assert len(data["mobile"]) == 3


def _write_minimal_png(path: Path) -> None:
    """Write a minimal valid 1x1 PNG file at ``path``."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    iend = chunk(b"IEND", b"")
    path.write_bytes(sig + ihdr + idat + iend)


def _write_minimal_sidecar(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "viewport": {"width": 1280, "height": 720},
                "target": {"kind": "single", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}},
            }
        ),
        encoding="utf-8",
    )


def test_build_config_layouts_default_to_first_when_unset():
    """When layouts is non-empty but defaultLayout is omitted, the model
    validator fills it with the first declared layout."""
    cfg = BuildConfig(
        scenariosRoot="./e2e/scenarios",
        outputDir="./out",
        assetsDir="./assets",
        role="editor",
        layouts=["desktop", "mobile"],
        site={"title": "X"},
        guides=[{"slug": "g", "title": "G", "scenario": "g.md"}],
        navbar={"order": ["g"]},
    )
    assert cfg.default_layout == "desktop"


def test_build_config_layouts_validates_default_layout_membership():
    """defaultLayout must be a member of layouts."""
    with pytest.raises(Exception, match="defaultLayout"):
        BuildConfig(
            scenariosRoot="./e2e/scenarios",
            outputDir="./out",
            assetsDir="./assets",
            role="editor",
            layouts=["desktop", "mobile"],
            defaultLayout="tablet",
            site={"title": "X"},
            guides=[{"slug": "g", "title": "G", "scenario": "g.md"}],
            navbar={"order": ["g"]},
        )


# ── Per-guide layout narrowing (scenario frontmatter `layouts:`) ─────────────


def _scenario_with_layouts(tmp_path: Path, declared) -> Path:
    """Copy the fixture's onboarding scenario into a tmp scenarios root, with a
    frontmatter ``layouts:`` declaration spliced in. The slug stays
    ``onboarding`` so the seeded screenshot tree still resolves."""
    src = (FIXTURE_ROOT / "e2e" / "scenarios" / "editor" / "onboarding.md").read_text(encoding="utf-8")
    if declared is not None:
        block = "layouts:\n" + "".join(f"  - {name}\n" for name in declared)
        # Splice in just before the closing frontmatter delimiter.
        head, sep, body = src.partition("\n---\n")
        src = head + "\n" + block.rstrip("\n") + sep + body
    root = tmp_path / "scenarios"
    (root / "editor").mkdir(parents=True, exist_ok=True)
    (root / "editor" / "onboarding.md").write_text(src, encoding="utf-8")
    return root


def _layouts_config(tmp_path, scenarios_root: Path, layouts: list) -> BuildConfig:
    screenshots_dir = tmp_path / "screenshots"
    _make_layout_screenshot_tree(screenshots_dir, "onboarding", ["fr"], ["desktop", "mobile"])
    return BuildConfig(
        scenariosRoot=str(scenarios_root),
        screenshotsDir=str(screenshots_dir),
        outputDir=str(tmp_path / "site"),
        assetsDir=str(tmp_path / "unused"),
        role="editor",
        locales=["fr"],
        defaultLocale="fr",
        layouts=layouts,
        defaultLayout=layouts[0],
        target="static",
        site={"title": "Demo Guides"},
        guides=[
            {"slug": "onboarding", "title": "Onboarding", "scenario": "editor/onboarding.md"},
        ],
        navbar={"order": ["onboarding"]},
    )


class TestPerGuideLayouts:
    """A scenario's frontmatter `layouts:` narrows the build's layout dimension
    for that guide — the build-side half of the scenario-level declaration.
    Without it a desktop-only flow ships a "Mobile" toggle that swaps between
    identical screenshots."""

    def test_declared_subset_drops_the_other_layouts(self, tmp_path):
        cfg = _layouts_config(tmp_path, _scenario_with_layouts(tmp_path, ["desktop"]), ["desktop", "mobile"])
        BuildAssembler(cfg, tmp_path).build_all()

        page = (tmp_path / "site" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert 'data-layout="desktop"' in page
        assert 'data-layout="mobile"' not in page
        # One layout ⇒ no toggle to render.
        assert "steppy-layout-switch" not in page

    def test_declared_subset_narrows_screenshot_copies(self, tmp_path):
        cfg = _layouts_config(tmp_path, _scenario_with_layouts(tmp_path, ["desktop"]), ["desktop", "mobile"])
        BuildAssembler(cfg, tmp_path).build_all()

        assets = tmp_path / "site" / "assets" / "onboarding" / "fr"
        assert (assets / "desktop" / "step-01.png").exists()
        assert not (assets / "mobile").exists()

    def test_manifest_reports_the_effective_layouts_per_guide(self, tmp_path):
        cfg = _layouts_config(tmp_path, _scenario_with_layouts(tmp_path, ["desktop"]), ["desktop", "mobile"])
        BuildAssembler(cfg, tmp_path).build_all()

        manifest = json.loads((tmp_path / "site" / "manifest.json").read_text(encoding="utf-8"))
        # Site-wide dimension is unchanged; the guide entry says what it built.
        assert manifest["layouts"] == ["desktop", "mobile"]
        assert manifest["guides"][0]["layouts"] == ["desktop"]

    def test_undeclared_scenario_keeps_every_build_layout(self, tmp_path):
        cfg = _layouts_config(tmp_path, _scenario_with_layouts(tmp_path, None), ["desktop", "mobile"])
        BuildAssembler(cfg, tmp_path).build_all()

        page = (tmp_path / "site" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert 'data-layout="desktop"' in page
        assert 'data-layout="mobile"' in page
        assert "steppy-layout-switch" in page

    def test_default_layout_falls_back_when_narrowed_away(self, tmp_path):
        """defaultLayout=desktop but the guide only declares mobile — the page
        must still mark a layout active."""
        cfg = _layouts_config(tmp_path, _scenario_with_layouts(tmp_path, ["mobile"]), ["desktop", "mobile"])
        BuildAssembler(cfg, tmp_path).build_all()

        page = (tmp_path / "site" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert 'class="steppy-layout steppy-layout-active" data-layout="mobile"' in page

    def test_disjoint_declaration_is_a_build_error(self, tmp_path):
        from steppy.build import BuildError

        cfg = _layouts_config(tmp_path, _scenario_with_layouts(tmp_path, ["desktop"]), ["mobile"])
        with pytest.raises(BuildError, match="no overlap"):
            BuildAssembler(cfg, tmp_path).build_all()

    def test_interactive_manifest_follows_the_narrowed_layouts(self, tmp_path):
        from steppy.build_models import GuideMode

        cfg = _layouts_config(tmp_path, _scenario_with_layouts(tmp_path, ["desktop"]), ["desktop", "mobile"])
        cfg.guides[0].modes = [GuideMode.STEPBYSTEP, GuideMode.INTERACTIVE]
        BuildAssembler(cfg, tmp_path).build_all()

        manifest = json.loads((tmp_path / "site" / "onboarding" / "guide.json").read_text(encoding="utf-8"))
        assert manifest["layouts"] == ["desktop"]
        assert manifest["defaultLayout"] == "desktop"


# ── PDF export (static target) ───────────────────────────────────────────────


def _patch_render_pdfs(monkeypatch, sink: list):
    """Record the jobs `build_all` would print, and fake a successful render."""
    import steppy.pdf as pdf_module

    def _fake(jobs, options=None, **kwargs):
        sink.extend(jobs)
        return [pdf_module.PdfResult(job=j, ok=True) for j in jobs]

    monkeypatch.setattr(pdf_module, "render_pdfs", _fake)


class TestBuildPdfExport:
    def test_dry_run_plans_pdfs_without_launching_a_browser(self, layouts_static_fixture_config, tmp_path, monkeypatch):
        import steppy.pdf as pdf_module

        def _boom(*args, **kwargs):
            raise AssertionError("a dry run must never launch a browser")

        monkeypatch.setattr(pdf_module, "render_pdfs", _boom)
        results = BuildAssembler(layouts_static_fixture_config, tmp_path).build_all(
            dry_run=True, pdf=True, handbook=True
        )

        planned = [p for p in results if p.endswith(".pdf")]
        assert planned, "no PDFs planned"
        assert all(results[p] == "planned" for p in planned)

    def test_pdf_flag_prints_every_page_and_links_it(self, layouts_static_fixture_config, tmp_path, monkeypatch):
        jobs_seen = []
        _patch_render_pdfs(monkeypatch, jobs_seen)
        BuildAssembler(layouts_static_fixture_config, tmp_path).build_all(pdf=True)

        assert {j.html.name for j in jobs_seen} == {"fr.html", "nl.html"}
        page = (tmp_path / "site" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert 'class="steppy-pdf-link" href="./fr.pdf"' in page

    def test_handbook_collects_the_guides_at_their_default_layout(
        self, layouts_static_fixture_config, tmp_path, monkeypatch
    ):
        jobs_seen = []
        _patch_render_pdfs(monkeypatch, jobs_seen)
        BuildAssembler(layouts_static_fixture_config, tmp_path).build_all(handbook=True)

        book = (tmp_path / "site" / "handbook" / "fr.html").read_text(encoding="utf-8")
        assert book.count('class="steppy-book-guide"') == 1
        # Fragments are reused verbatim, so screenshot URLs keep the ../assets
        # depth the per-guide pages use.
        assert "../assets/onboarding/" in book
        assert (tmp_path / "site" / "handbook" / "index.html").exists()
        assert {j.html.name for j in jobs_seen} == {"fr.html", "nl.html"}

    def test_handbook_is_linked_from_the_index(self, layouts_static_fixture_config, tmp_path, monkeypatch):
        _patch_render_pdfs(monkeypatch, [])
        BuildAssembler(layouts_static_fixture_config, tmp_path).build_all(handbook=True)

        index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        assert "handbook/fr.html" in index
        assert "handbook/fr.pdf" in index

    def test_no_flags_emits_no_pdf_artifacts(self, layouts_static_fixture_config, tmp_path):
        results = BuildAssembler(layouts_static_fixture_config, tmp_path).build_all()
        assert not [p for p in results if p.endswith(".pdf")]
        page = (tmp_path / "site" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert "steppy-pdf-link" not in page

    def test_angular_target_rejects_pdf_export(self, layouts_fixture_config, tmp_path):
        from steppy.build import BuildError

        with pytest.raises(BuildError, match="target: 'static'"):
            BuildAssembler(layouts_fixture_config, tmp_path).build_all(pdf=True)

    def test_handbook_slug_collision_is_reported(self, layouts_static_fixture_config, tmp_path):
        from steppy.build import BuildError
        from steppy.build_models import GuideEntry

        cfg = layouts_static_fixture_config
        cfg.guides.append(GuideEntry(slug="handbook", title="Handbook", scenario="editor/invoices.md"))
        with pytest.raises(BuildError, match="handbook"):
            BuildAssembler(cfg, tmp_path).build_all(handbook=True)
