"""Unit tests for the build assembler module."""

import json
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.build import BuildAssembler, BuildError, _slug_to_class_name, pick_locale
from steppy.build_models import BuildConfig


# ── pick_locale ──────────────────────────────────────────────────────────────


class TestPickLocale:
    def test_returns_plain_string_as_is(self):
        assert pick_locale("Hello", "fr", "fr") == "Hello"
        assert pick_locale("Hello", "nl", "fr") == "Hello"

    def test_picks_requested_locale_from_map(self):
        m = {"fr": "Bonjour", "nl": "Hallo"}
        assert pick_locale(m, "fr", "fr") == "Bonjour"
        assert pick_locale(m, "nl", "fr") == "Hallo"

    def test_falls_back_to_default_locale(self):
        m = {"fr": "Bonjour"}
        assert pick_locale(m, "nl", "fr") == "Bonjour"

    def test_returns_none_when_neither_locale_present(self):
        m = {"de": "Hallo"}
        assert pick_locale(m, "nl", "fr") is None

    def test_none_input_returns_none(self):
        assert pick_locale(None, "fr", "fr") is None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_scenario(tmp_path: Path, rel_path: str, *, role: str = "editor") -> Path:
    """Create a minimal scenario .md file with guide-text for fr and nl."""
    path = tmp_path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        dedent(f"""\
        ---
        title: Test Scenario
        tags:
          - e2e
          - user-guide-{role}
        guide:
          enabled: true
        ---

        ### 1. Navigate to dashboard
        - role: {role}
        - action: navigate
        - target: /dashboard
        - screenshot: {role}
        - guide-text:
          fr: "Accueil du tableau de bord"
          nl: "Dashboard startpagina"

        ### 2. Click button
        - role: {role}
        - action: click
        - selector: button#submit
        - screenshot: {role}
        - guide-text:
          fr: "Cliquez sur le bouton"
          nl: "Klik op de knop"
        """),
        encoding="utf-8",
    )
    return path


def _make_screenshots(tmp_path: Path, scenario_slug: str, count: int = 2) -> Path:
    """Create dummy screenshot PNG files and JSON sidecars."""
    screenshots_dir = tmp_path / "screenshots"
    scenario_dir = screenshots_dir / scenario_slug
    scenario_dir.mkdir(parents=True, exist_ok=True)

    # Minimal valid PNG (1x1 pixel, white)
    # PNG signature + IHDR + IDAT + IEND
    import struct
    import zlib

    def _minimal_png() -> bytes:
        sig = b"\x89PNG\r\n\x1a\n"

        def chunk(ctype, data):
            c = ctype + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        raw = zlib.compress(b"\x00\xff\xff\xff")
        idat = chunk(b"IDAT", raw)
        iend = chunk(b"IEND", b"")
        return sig + ihdr + idat + iend

    png_data = _minimal_png()

    for i in range(1, count + 1):
        (scenario_dir / f"step-{i:02d}.png").write_bytes(png_data)
        sidecar = {
            "viewport": {"width": 1280, "height": 720},
            "target": {"kind": "single", "bbox": {"x": 100, "y": 200, "width": 50, "height": 30}},
        }
        (scenario_dir / f"step-{i:02d}.json").write_text(json.dumps(sidecar), encoding="utf-8")

    return screenshots_dir


def _make_config(tmp_path: Path, **overrides) -> BuildConfig:
    """Return a valid BuildConfig pointing at tmp_path-relative dirs."""
    base = {
        "scenariosRoot": "./scenarios",
        "screenshotsDir": "./screenshots",
        "outputDir": "./output/generated",
        "assetsDir": "./output/assets",
        "role": "editor",
        "locales": ["fr", "nl"],
        "defaultLocale": "fr",
        "site": {"title": "Test Guides"},
        # The production default target is now ``static`` (see
        # test_build_models.test_target_defaults_to_static). This helper pins
        # ``angular`` because most tests here assert Angular output; the static
        # test classes override it with ``target="static"`` explicitly.
        "target": "angular",
        "guides": [
            {"slug": "onboarding", "title": "Onboarding", "scenario": "onboarding.md"},
        ],
        "navbar": {"order": ["onboarding"]},
    }
    base.update(overrides)
    return BuildConfig(**base)


def _setup_project(tmp_path: Path, **config_overrides) -> tuple[BuildConfig, Path]:
    """Create a complete test project with scenarios, screenshots, and config."""
    _make_scenario(tmp_path / "scenarios", "onboarding.md")
    _make_screenshots(tmp_path, "onboarding")
    config = _make_config(tmp_path, **config_overrides)
    return config, tmp_path


# ── Tests: slug_to_class_name ────────────────────────────────────────────────


class TestSlugToClassName:
    def test_simple(self):
        assert _slug_to_class_name("onboarding") == "Onboarding"

    def test_hyphenated(self):
        assert _slug_to_class_name("my-guide") == "MyGuide"

    def test_underscored(self):
        assert _slug_to_class_name("my_guide") == "MyGuide"

    def test_mixed(self):
        assert _slug_to_class_name("my-long_guide") == "MyLongGuide"


# ── Tests: BuildAssembler ────────────────────────────────────────────────────


class TestBuildAssembler:
    def test_generates_all_expected_files(self, tmp_path):
        config, root = _setup_project(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        output_dir = root / "output" / "generated"
        expected_files = [
            "editor.routes.ts",
            "editor.nav.json",
            "i18n/editor/fr.json",
            "i18n/editor/nl.json",
            ".gitignore",
            "onboarding/onboarding.component.ts",
            "onboarding/onboarding.content.ts",
            "onboarding/onboarding-stepbystep-fr.html",
            "onboarding/onboarding-stepbystep-nl.html",
        ]
        for f in expected_files:
            assert (output_dir / f).exists(), f"Missing: {f}"

        # Dropped artifacts must not be generated
        for dropped in (
            "index.ts",
            "guides.routes.ts",
            "guides.navbar.ts",
            "guides.manifest.ts",
            "locale.ts",
            "layout.ts",
        ):
            assert not (output_dir / dropped).exists(), f"Should not exist: {dropped}"

    def test_generated_header(self, tmp_path):
        config, root = _setup_project(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        output_dir = root / "output" / "generated"
        component = (output_dir / "onboarding" / "onboarding.component.ts").read_text(encoding="utf-8")
        assert component.startswith("// GENERATED BY STEPPY")

    def test_routes_has_lazy_load(self, tmp_path):
        config, root = _setup_project(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        routes = (root / "output" / "generated" / "editor.routes.ts").read_text(encoding="utf-8")
        assert "loadComponent" in routes
        assert "'onboarding'" in routes
        assert "OnboardingComponent" in routes
        assert "EDITOR_ROUTES" in routes

    def test_navbar_follows_order(self, tmp_path):
        _make_scenario(tmp_path / "scenarios", "onboarding.md")
        _make_scenario(tmp_path / "scenarios", "invoices.md")
        _make_screenshots(tmp_path, "onboarding")
        _make_screenshots(tmp_path, "invoices")

        config = _make_config(
            tmp_path,
            guides=[
                {"slug": "onboarding", "title": "Onboarding", "scenario": "onboarding.md"},
                {"slug": "invoices", "title": "Invoices", "scenario": "invoices.md"},
            ],
            navbar={"order": ["invoices", "onboarding"]},
        )
        assembler = BuildAssembler(config, tmp_path)
        assembler.build_all()

        nav = json.loads((tmp_path / "output" / "generated" / "editor.nav.json").read_text(encoding="utf-8"))
        slugs = [n["slug"] for n in nav]
        assert slugs == ["invoices", "onboarding"], "nav.json should follow configured order"
        # Translation-key shape — never inlined LocalizedText
        for node in nav:
            assert "titleKey" in node
            assert node["titleKey"].startswith("EDITOR.guides.")

    def test_i18n_lists_all_guides(self, tmp_path):
        config, root = _setup_project(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        bundle = json.loads((root / "output" / "generated" / "i18n" / "editor" / "fr.json").read_text(encoding="utf-8"))
        assert "EDITOR" in bundle
        ns = bundle["EDITOR"]
        assert ns["site"]["title"] == "Test Guides"
        assert "onboarding" in ns["guides"]
        assert ns["guides"]["onboarding"]["title"] == "Onboarding"
        # Hardcoded UI defaults shipped in every locale
        assert "groupEmpty" in ns["ui"]
        assert "stepLabel" in ns["onboarding"]
        # ngx-translate placeholder syntax preserved
        assert "{{position}}" in ns["onboarding"]["stepLabel"]

    def test_multi_mode_generates_interactive_component(self, tmp_path):
        """Stage 4: the interactive Angular component is no longer a
        "Coming soon" placeholder — it mounts the ``<steppy-guide>`` web
        component and bridges ngx-translate's locale into it."""
        _make_scenario(tmp_path / "scenarios", "onboarding.md")
        _make_screenshots(tmp_path, "onboarding")

        config = _make_config(
            tmp_path,
            guides=[
                {
                    "slug": "onboarding",
                    "title": "Onboarding",
                    "scenario": "onboarding.md",
                    "modes": ["stepbystep", "interactive"],
                },
            ],
        )
        assembler = BuildAssembler(config, tmp_path)
        assembler.build_all()

        output_dir = tmp_path / "output" / "generated"
        interactive = output_dir / "onboarding" / "onboarding-interactive.component.ts"
        assert interactive.exists()

        content = interactive.read_text(encoding="utf-8")
        assert "Coming soon" not in content
        assert "OnboardingInteractiveComponent" in content
        assert "<steppy-guide" in content
        assert "/assets/editor/onboarding/guide.json" in content

    def test_single_mode_no_mode_switcher(self, tmp_path):
        config, root = _setup_project(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        component = (root / "output" / "generated" / "onboarding" / "onboarding.component.ts").read_text(
            encoding="utf-8"
        )
        assert "modeOptions" not in component

    def test_multi_mode_has_mode_switcher(self, tmp_path):
        _make_scenario(tmp_path / "scenarios", "onboarding.md")
        _make_screenshots(tmp_path, "onboarding")

        config = _make_config(
            tmp_path,
            guides=[
                {
                    "slug": "onboarding",
                    "title": "Onboarding",
                    "scenario": "onboarding.md",
                    "modes": ["stepbystep", "interactive"],
                },
            ],
        )
        assembler = BuildAssembler(config, tmp_path)
        assembler.build_all()

        component = (tmp_path / "output" / "generated" / "onboarding" / "onboarding.component.ts").read_text(
            encoding="utf-8"
        )
        assert "modeOptions" in component
        assert "SelectButtonModule" in component

    def test_component_has_no_locale_selector(self, tmp_path):
        """Locale switching is owned by the host app via ngx-translate; the
        generated component never renders its own locale toggle, even when
        multiple locales are configured."""
        config, root = _setup_project(tmp_path, locales=["fr", "nl"])
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        component = (root / "output" / "generated" / "onboarding" / "onboarding.component.ts").read_text(
            encoding="utf-8"
        )
        assert "localeOptions" not in component
        # No GuidesLocaleService import \u2014 the component reads the active
        # locale from TranslateService instead.
        assert "GuidesLocaleService" not in component
        assert "TranslateService" in component
        assert "TranslatePipe" in component

    def test_content_has_html_per_locale(self, tmp_path):
        config, root = _setup_project(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        content = (root / "output" / "generated" / "onboarding" / "onboarding.content.ts").read_text(encoding="utf-8")
        assert "'fr':" in content
        assert "'nl':" in content
        assert "steppy-guide" in content  # HTML fragment CSS class

    def test_deterministic_output(self, tmp_path):
        config, root = _setup_project(tmp_path)

        # First build
        assembler1 = BuildAssembler(config, root)
        assembler1.build_all()
        files1 = {}
        output_dir = root / "output" / "generated"
        for f in output_dir.rglob("*"):
            if f.is_file():
                files1[str(f.relative_to(output_dir))] = f.read_text(encoding="utf-8")

        # Second build
        assembler2 = BuildAssembler(config, root)
        assembler2.build_all()
        files2 = {}
        for f in output_dir.rglob("*"):
            if f.is_file():
                files2[str(f.relative_to(output_dir))] = f.read_text(encoding="utf-8")

        assert files1 == files2, "Output should be identical across builds"

    def test_dry_run_writes_nothing(self, tmp_path):
        config, root = _setup_project(tmp_path)
        assembler = BuildAssembler(config, root)
        results = assembler.build_all(dry_run=True)

        output_dir = root / "output" / "generated"
        assert not output_dir.exists()

        # Results should still report planned files
        assert len(results) > 0
        assert all(s == "planned" for s in results.values())

    def test_screenshots_copied_to_assets(self, tmp_path):
        config, root = _setup_project(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        assets_dir = root / "output" / "assets" / "onboarding"
        assert assets_dir.exists()
        assert (assets_dir / "step-01.png").exists()
        assert (assets_dir / "step-02.png").exists()
        # Bbox sidecars must travel alongside the PNGs — the runtime cursor
        # animation reads them.
        assert (assets_dir / "step-01.json").exists()
        assert (assets_dir / "step-02.json").exists()

    def test_html_references_role_scoped_assets_path(self, tmp_path):
        """Image URLs in the rendered HTML fragment are absolute and
        prefixed by ``/assets/<role>/`` so they resolve from any host
        route depth."""
        config, root = _setup_project(tmp_path, role="editor")
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        html = (root / "output" / "generated" / "onboarding" / "onboarding-stepbystep-fr.html").read_text(
            encoding="utf-8"
        )
        assert "/assets/editor/onboarding/step-" in html


class TestBuildAssemblerErrors:
    def test_missing_scenarios_root(self, tmp_path):
        config = _make_config(tmp_path, scenariosRoot="./nonexistent")
        assembler = BuildAssembler(config, tmp_path)
        with pytest.raises(BuildError, match="Scenarios root not found"):
            assembler.build_all()

    def test_missing_screenshots_dir(self, tmp_path):
        _make_scenario(tmp_path / "scenarios", "onboarding.md")
        config = _make_config(tmp_path, screenshotsDir="./nonexistent")
        assembler = BuildAssembler(config, tmp_path)
        with pytest.raises(BuildError, match="Screenshots directory not found"):
            assembler.build_all()

    def test_missing_scenario_file(self, tmp_path):
        (tmp_path / "scenarios").mkdir(parents=True, exist_ok=True)
        _make_screenshots(tmp_path, "onboarding")
        config = _make_config(tmp_path)
        assembler = BuildAssembler(config, tmp_path)
        with pytest.raises(BuildError, match="Scenario file not found"):
            assembler.build_all()

    def test_gitignore_written(self, tmp_path):
        config, root = _setup_project(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        gitignore = (root / "output" / "generated" / ".gitignore").read_text(encoding="utf-8")
        assert "*" in gitignore


# ── Tests: BuildAssembler with target='static' ───────────────────────────────


class TestBuildAssemblerStatic:
    def _setup(self, tmp_path: Path, **overrides) -> tuple[BuildConfig, Path]:
        _make_scenario(tmp_path / "scenarios", "onboarding.md")
        _make_screenshots(tmp_path, "onboarding")
        overrides.setdefault("target", "static")
        config = _make_config(tmp_path, **overrides)
        return config, tmp_path

    def test_emits_only_static_artifacts(self, tmp_path):
        config, root = self._setup(tmp_path)
        BuildAssembler(config, root).build_all()

        output_dir = root / "output" / "generated"

        # Static-only artifacts present
        assert (output_dir / "index.html").exists()
        assert (output_dir / "manifest.json").exists()
        assert (output_dir / "assets" / "guides.css").exists()
        assert (output_dir / ".nojekyll").exists()
        assert (output_dir / "onboarding" / "fr.html").exists()
        assert (output_dir / "onboarding" / "nl.html").exists()

        # Angular artifacts absent
        assert not (output_dir / "editor.routes.ts").exists()
        assert not (output_dir / "editor.nav.json").exists()
        assert not (output_dir / "i18n").exists()
        assert not (output_dir / ".gitignore").exists()
        assert not (output_dir / "onboarding" / "onboarding.component.ts").exists()
        assert not (output_dir / "onboarding" / "onboarding.content.ts").exists()
        # Angular-style fragment file is suppressed for static target
        assert not (output_dir / "onboarding" / "onboarding-stepbystep-fr.html").exists()

    def test_page_embeds_fragment_and_links_external_css(self, tmp_path):
        config, root = self._setup(tmp_path)
        BuildAssembler(config, root).build_all()

        page = (root / "output" / "generated" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert page.startswith("<!doctype html>")
        assert 'class="steppy-guide"' in page  # fragment embedded
        assert '<link rel="stylesheet" href="../assets/guides.css">' in page
        # No inline <style> block from the fragment (suppressed for static)
        assert "<style>" not in page

    def test_screenshots_copied_to_self_contained_assets(self, tmp_path):
        config, root = self._setup(tmp_path)
        BuildAssembler(config, root).build_all()

        # Static target writes screenshots to outputDir/assets/<slug>/, NOT the configured assetsDir
        assets = root / "output" / "generated" / "assets" / "onboarding"
        assert (assets / "step-01.png").exists()
        assert (assets / "step-02.png").exists()
        assert (assets / "step-01.json").exists()
        assert (assets / "step-02.json").exists()
        # Configured assetsDir should not exist (static is self-contained)
        assert not (root / "output" / "assets").exists()

    def test_index_lists_guides_in_navbar_order(self, tmp_path):
        _make_scenario(tmp_path / "scenarios", "onboarding.md")
        _make_scenario(tmp_path / "scenarios", "invoices.md")
        _make_screenshots(tmp_path, "onboarding")
        _make_screenshots(tmp_path, "invoices")

        config = _make_config(
            tmp_path,
            target="static",
            guides=[
                {"slug": "onboarding", "title": "Onboarding", "scenario": "onboarding.md"},
                {"slug": "invoices", "title": "Invoices", "scenario": "invoices.md"},
            ],
            navbar={"order": ["invoices", "onboarding"]},
        )
        BuildAssembler(config, tmp_path).build_all()

        index = (tmp_path / "output" / "generated" / "index.html").read_text(encoding="utf-8")
        idx_presc = index.index("invoices/")
        idx_onboard = index.index("onboarding/")
        assert idx_presc < idx_onboard, "Index should follow navbar.order"

    def test_manifest_json_is_valid_and_lists_pages(self, tmp_path):
        config, root = self._setup(tmp_path)
        BuildAssembler(config, root).build_all()

        manifest = json.loads((root / "output" / "generated" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["site"]["title"] == "Test Guides"
        assert manifest["defaultLocale"] == "fr"
        assert manifest["locales"] == ["fr", "nl"]
        assert len(manifest["guides"]) == 1
        guide = manifest["guides"][0]
        assert guide["slug"] == "onboarding"
        assert guide["pages"] == {"fr": "onboarding/fr.html", "nl": "onboarding/nl.html"}

    def test_nojekyll_is_empty(self, tmp_path):
        config, root = self._setup(tmp_path)
        BuildAssembler(config, root).build_all()

        nojekyll = root / "output" / "generated" / ".nojekyll"
        assert nojekyll.read_bytes() == b""

    def test_locale_switcher_only_when_multiple_locales(self, tmp_path):
        # Single locale → no switcher
        config, root = self._setup(tmp_path, locales=["fr"], defaultLocale="fr")
        BuildAssembler(config, root).build_all()
        page = (root / "output" / "generated" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert "steppy-locale-switch" not in page

    def test_locale_switcher_present_for_multi_locale(self, tmp_path):
        config, root = self._setup(tmp_path)  # default locales=["fr","nl"]
        BuildAssembler(config, root).build_all()
        page = (root / "output" / "generated" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert "steppy-locale-switch" in page
        # Current locale rendered as a span (aria-current), other as <a>
        assert 'aria-current="true"' in page
        assert 'href="./nl.html"' in page

    def test_dry_run_writes_nothing(self, tmp_path):
        config, root = self._setup(tmp_path)
        results = BuildAssembler(config, root).build_all(dry_run=True)

        assert not (root / "output" / "generated").exists()
        assert results
        assert all(s == "planned" for s in results.values())


# ── Tests: fragment template back-compat ─────────────────────────────────────


class TestFragmentTemplate:
    def test_inline_style_default_on(self, tmp_path):
        """Fragment template with no include_styles var must still emit the
        inline <style> block — preserves all existing callers (steppy guide,
        Angular content.ts.j2)."""
        import jinja2

        bundled = Path(__import__("steppy").__file__).parent / "templates"
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(bundled)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # markdown filter required by the template
        env.filters["markdown"] = lambda s: s
        tpl = env.get_template("guide.fragment.html.j2")
        rendered = tpl.render(title="X", role="Editor", steps=[])
        assert "<style>" in rendered

    def test_include_styles_false_suppresses_style_block(self, tmp_path):
        import jinja2

        bundled = Path(__import__("steppy").__file__).parent / "templates"
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(bundled)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        env.filters["markdown"] = lambda s: s
        tpl = env.get_template("guide.fragment.html.j2")
        rendered = tpl.render(title="X", role="Editor", steps=[], include_styles=False)
        assert "<style>" not in rendered
        # But the actual content div should still be there
        assert 'class="steppy-guide"' in rendered


# ── Groups + onboarding (Angular target) ─────────────────────────────────────


def _setup_grouped_project(tmp_path: Path) -> tuple[BuildConfig, Path]:
    """Project with: an ungrouped 'intro' guide, two 'module' grouped guides
    'a' and 'b', and onboarding through ['intro', 'a']."""
    for slug in ("intro", "a", "b"):
        _make_scenario(tmp_path / "scenarios", f"{slug}.md")
        _make_screenshots(tmp_path, slug)
    config = _make_config(
        tmp_path,
        guides=[
            {"slug": "intro", "title": "Intro", "scenario": "intro.md"},
            {"slug": "a", "title": "A", "scenario": "a.md", "group": "module"},
            {"slug": "b", "title": "B", "scenario": "b.md", "group": "module"},
        ],
        groups=[{"slug": "module", "title": "Module", "description": "Group of two."}],
        navbar={"order": ["intro", "module"]},
        onboarding={
            "enabled": True,
            "title": "Getting Started",
            "guides": ["intro", "a"],
        },
    )
    return config, tmp_path


class TestGroupsAndOnboardingAngular:
    def test_nav_json_uses_navigation_tree(self, tmp_path):
        config, root = _setup_grouped_project(tmp_path)
        BuildAssembler(config, root).build_all()
        nav = json.loads((root / "output" / "generated" / "editor.nav.json").read_text(encoding="utf-8"))
        # Top-level kinds: onboarding → guide (intro) → group (module)
        kinds = [n["kind"] for n in nav]
        assert kinds == ["onboarding", "guide", "group"]
        # Title fields are translation keys, not inlined LocalizedText
        intro = next(n for n in nav if n["kind"] == "guide")
        module = next(n for n in nav if n["kind"] == "group")
        assert intro["titleKey"] == "EDITOR.guides.intro.title"
        assert module["titleKey"] == "EDITOR.groups.module.title"
        assert module["descriptionKey"] == "EDITOR.groups.module.description"
        # Group children carry their own titleKeys
        child_keys = [c["titleKey"] for c in module["children"]]
        assert child_keys == ["EDITOR.guides.a.title", "EDITOR.guides.b.title"]
        # Top-level ungrouped guide carries enriched metadata: explicit null
        # group, default mode, and its onboarding slot (intro is step 0).
        assert intro["group"] is None
        assert intro["modes"] == ["stepbystep"]
        assert intro["onboardingIndex"] == 0
        # Grouped children carry their parent group slug, and only 'a'
        # participates in onboarding (slot 1); 'b' is null.
        child_a = next(c for c in module["children"] if c["slug"] == "a")
        child_b = next(c for c in module["children"] if c["slug"] == "b")
        assert child_a["group"] == "module"
        assert child_a["modes"] == ["stepbystep"]
        assert child_a["onboardingIndex"] == 1
        assert child_b["group"] == "module"
        assert child_b["onboardingIndex"] is None

    def test_i18n_carries_group_titles_and_onboarding_text(self, tmp_path):
        config, root = _setup_grouped_project(tmp_path)
        BuildAssembler(config, root).build_all()
        bundle = json.loads((root / "output" / "generated" / "i18n" / "editor" / "fr.json").read_text(encoding="utf-8"))
        ns = bundle["EDITOR"]
        assert ns["groups"]["module"]["title"] == "Module"
        assert ns["groups"]["module"]["description"] == "Group of two."
        # Onboarding title/description resolved per-locale (default 'Getting Started')
        assert ns["onboarding"]["title"] == "Getting Started"
        # UI defaults present
        assert ns["onboarding"]["start"]
        assert ns["ui"]["groupEmpty"]

    def test_routes_include_group_and_onboarding(self, tmp_path):
        config, root = _setup_grouped_project(tmp_path)
        BuildAssembler(config, root).build_all()
        routes = (root / "output" / "generated" / "editor.routes.ts").read_text(encoding="utf-8")
        assert "ModuleGroupComponent" in routes
        assert "OnboardingComponent" in routes
        assert "path: 'getting-started'" in routes
        # Onboarding is the default redirect when present
        assert "redirectTo: 'getting-started'" in routes
        # Role-scoped routes constant
        assert "EDITOR_ROUTES" in routes

    def test_group_and_onboarding_components_emitted(self, tmp_path):
        config, root = _setup_grouped_project(tmp_path)
        BuildAssembler(config, root).build_all()
        gen = root / "output" / "generated"
        assert (gen / "_groups" / "module.group.component.ts").exists()
        assert (gen / "_onboarding" / "onboarding.component.ts").exists()

    def test_onboarding_strip_only_on_member_guides(self, tmp_path):
        config, root = _setup_grouped_project(tmp_path)
        BuildAssembler(config, root).build_all()
        gen = root / "output" / "generated"
        # 'intro' (step 0) and 'a' (step 1) → strip present; 'b' → no strip
        intro = (gen / "intro" / "intro.component.ts").read_text(encoding="utf-8")
        a = (gen / "a" / "a.component.ts").read_text(encoding="utf-8")
        b = (gen / "b" / "b.component.ts").read_text(encoding="utf-8")
        assert "steppy-onboarding-strip" in intro
        assert "steppy-onboarding-strip" in a
        assert "steppy-onboarding-strip" not in b


# ── Groups + onboarding (Static target) ──────────────────────────────────────


def _setup_grouped_static_project(tmp_path: Path) -> tuple[BuildConfig, Path]:
    for slug in ("intro", "a", "b"):
        _make_scenario(tmp_path / "scenarios", f"{slug}.md")
        _make_screenshots(tmp_path, slug)
    config = _make_config(
        tmp_path,
        target="static",
        guides=[
            {"slug": "intro", "title": "Intro", "scenario": "intro.md"},
            {"slug": "a", "title": "A", "scenario": "a.md", "group": "module"},
            {"slug": "b", "title": "B", "scenario": "b.md", "group": "module"},
        ],
        groups=[{"slug": "module", "title": "Module", "description": "Two guides."}],
        navbar={"order": ["intro", "module"]},
        onboarding={
            "enabled": True,
            "guides": ["intro", "a", "b"],
        },
    )
    return config, tmp_path


class TestGroupsAndOnboardingStatic:
    def test_group_landing_page_emitted(self, tmp_path):
        config, root = _setup_grouped_static_project(tmp_path)
        BuildAssembler(config, root).build_all()
        # Per-locale page
        page = root / "output" / "generated" / "module" / "fr.html"
        assert page.exists()
        html = page.read_text(encoding="utf-8")
        # Lists both group children at the current locale
        assert "../a/fr.html" in html
        assert "../b/fr.html" in html
        # Description rendered
        assert "Two guides." in html
        # index.html is now a redirect to the default-locale page
        redirect = (root / "output" / "generated" / "module" / "index.html").read_text(encoding="utf-8")
        assert "./fr.html" in redirect

    def test_onboarding_landing_page_emitted(self, tmp_path):
        config, root = _setup_grouped_static_project(tmp_path)
        BuildAssembler(config, root).build_all()
        page = root / "output" / "generated" / "getting-started" / "fr.html"
        assert page.exists()
        html = page.read_text(encoding="utf-8")
        # Step links in order
        assert html.index("../intro/") < html.index("../a/") < html.index("../b/")
        # Start CTA points to first step at current locale
        assert "../intro/fr.html?onboarding=1" in html
        # index.html redirects to default-locale page
        redirect = (root / "output" / "generated" / "getting-started" / "index.html").read_text(encoding="utf-8")
        assert "./fr.html" in redirect

    def test_index_renders_navigation_tree(self, tmp_path):
        config, root = _setup_grouped_static_project(tmp_path)
        BuildAssembler(config, root).build_all()
        index = (root / "output" / "generated" / "index.html").read_text(encoding="utf-8")
        # Onboarding card links to the per-locale landing page (default locale = fr)
        assert "./getting-started/fr.html" in index
        # Group card links to the per-locale group landing page
        assert "./module/fr.html" in index
        # Ungrouped guide links straight to its locale page
        assert "./intro/fr.html" in index
        # Grouped guides do NOT appear at top level
        assert "./a/fr.html" not in index
        assert "./b/fr.html" not in index

    def test_per_guide_strip_on_onboarding_members(self, tmp_path):
        config, root = _setup_grouped_static_project(tmp_path)
        BuildAssembler(config, root).build_all()
        a_page = (root / "output" / "generated" / "a" / "fr.html").read_text(encoding="utf-8")
        b_page = (root / "output" / "generated" / "b" / "fr.html").read_text(encoding="utf-8")
        # Both 'a' and 'b' are in onboarding
        assert "steppy-onboarding-strip" in a_page
        assert "steppy-onboarding-strip" in b_page
        # Last step ('b') has Finish, no next link
        assert "Finish" in b_page

    def test_grouped_guide_breadcrumb_shows_group(self, tmp_path):
        config, root = _setup_grouped_static_project(tmp_path)
        BuildAssembler(config, root).build_all()
        a_page = (root / "output" / "generated" / "a" / "fr.html").read_text(encoding="utf-8")
        # Breadcrumb contains group link to the same locale's page
        assert "../module/fr.html" in a_page

    def test_manifest_includes_groups_onboarding_navigation(self, tmp_path):
        config, root = _setup_grouped_static_project(tmp_path)
        BuildAssembler(config, root).build_all()
        manifest = json.loads((root / "output" / "generated" / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["groups"][0]["slug"] == "module"
        assert manifest["groups"][0]["guides"] == ["a", "b"]
        # New: pages mapping keyed by locale
        assert manifest["groups"][0]["pages"]["fr"] == "module/fr.html"
        assert manifest["onboarding"]["guides"] == ["intro", "a", "b"]
        assert manifest["onboarding"]["pages"]["fr"] == "getting-started/fr.html"
        # Per-guide group/onboardingIndex are surfaced
        guides_by_slug = {g["slug"]: g for g in manifest["guides"]}
        assert guides_by_slug["a"]["group"] == "module"
        assert guides_by_slug["a"]["onboardingIndex"] == 1
        assert guides_by_slug["intro"]["group"] is None
        # Navigation tree has expected top-level kinds
        kinds = [n["kind"] for n in manifest["navigation"]]
        assert kinds == ["onboarding", "guide", "group"]


class TestStaticSubGroups:
    def _setup(self, tmp_path: Path) -> tuple[BuildConfig, Path]:
        for slug in ("a", "b"):
            _make_scenario(tmp_path / "scenarios", f"{slug}.md")
            _make_screenshots(tmp_path, slug)
        config = _make_config(
            tmp_path,
            target="static",
            guides=[
                {"slug": "a", "title": "A", "scenario": "a.md", "group": "sub"},
                {"slug": "b", "title": "B", "scenario": "b.md", "group": "parent"},
            ],
            groups=[
                {"slug": "parent", "title": "Parent", "description": "Top group."},
                {"slug": "sub", "title": "Submodule", "parent": "parent"},
            ],
            navbar={"order": ["parent"]},
        )
        return config, tmp_path

    def test_parent_landing_lists_subgroup_card_and_direct_guide(self, tmp_path):
        config, root = self._setup(tmp_path)
        BuildAssembler(config, root).build_all()
        parent_page = (root / "output" / "generated" / "parent" / "fr.html").read_text(encoding="utf-8")
        # Sub-group card with "Module" eyebrow + child count + link to sub landing
        assert "steppy-index-group" in parent_page
        assert "Module" in parent_page
        assert "../sub/fr.html" in parent_page
        assert "1 guides" in parent_page
        # Direct guide of the parent group still appears
        assert "../b/fr.html" in parent_page
        # Sub-group's own landing page exists and links to its guide
        sub_page = (root / "output" / "generated" / "sub" / "fr.html").read_text(encoding="utf-8")
        assert "../a/fr.html" in sub_page

    def test_grouped_guide_breadcrumb_lands_on_subgroup(self, tmp_path):
        config, root = self._setup(tmp_path)
        BuildAssembler(config, root).build_all()
        a_page = (root / "output" / "generated" / "a" / "fr.html").read_text(encoding="utf-8")
        # Breadcrumb links to the immediate parent (the sub-group), not the top-level group
        assert "../sub/fr.html" in a_page
