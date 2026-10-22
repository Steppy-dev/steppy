"""Smoke tests for the stage-4 wiring of interactive guides on both targets.

Verifies that ``steppy build`` for a guide opted into ``modes: ['interactive']``
produces the manifest, the per-target host (component or HTML page), and the
player bundle at the right paths — and that turning interactive off again
suppresses the bundle copy.
"""

import json
import struct
import zlib
from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import ValidationError

from steppy.build import BuildAssembler
from steppy.build_models import BuildConfig


# ── Fixture helpers (mirror ``tests/test_build.py``) ────────────────────────


def _minimal_png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _write_scenario(tmp_path: Path) -> None:
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir(parents=True, exist_ok=True)
    (scenarios / "onboarding.md").write_text(
        dedent("""\
        ---
        title: Onboarding
        tags: [user-guide-editor]
        guide:
          enabled: true
        ---

        ### 1. Click new
        - role: editor
        - action: click
        - selector: '[data-testid="new"]'
        - screenshot: editor
        - guide-text:
            fr: "Cliquez sur Nouveau"
            nl: "Klik op Nieuw"

        ### 2. Navigate
        - role: editor
        - action: navigate
        - target: /next
        - screenshot: editor
        - guide-text:
            fr: "Page suivante"
            nl: "Volgende pagina"
        """),
        encoding="utf-8",
    )


def _write_screenshots(
    tmp_path: Path,
    slug: str = "onboarding",
    locales: tuple[str, ...] = ("fr", "nl"),
    layouts: tuple[str, ...] = (),
) -> None:
    """Write step-NN PNG + sidecar for every (locale, layout) combination."""
    base = tmp_path / "screenshots" / slug
    bbox = {"x": 100, "y": 200, "width": 50, "height": 30}
    layout_iter = layouts if layouts else (None,)
    for locale in locales:
        for layout in layout_iter:
            folder = base / locale
            if layout is not None:
                folder = folder / layout
            folder.mkdir(parents=True, exist_ok=True)
            for idx in (1, 2):
                (folder / f"step-{idx:02d}.png").write_bytes(_minimal_png())
                sidecar = {
                    "viewport": {"width": 1280, "height": 720},
                    # Step 1 is a click → single target with bbox.
                    # Step 2 is a navigate → target=None.
                    "target": ({"kind": "single", "bbox": bbox, "selector": "x"} if idx == 1 else None),
                }
                (folder / f"step-{idx:02d}.json").write_text(json.dumps(sidecar), encoding="utf-8")


def _make_config(tmp_path: Path, **overrides) -> BuildConfig:
    base = {
        "scenariosRoot": "./scenarios",
        "screenshotsDir": "./screenshots",
        "outputDir": "./output/generated",
        "assetsDir": "./output/assets",
        "role": "editor",
        "locales": ["fr", "nl"],
        "defaultLocale": "fr",
        "site": {"title": "Demo Guides"},
        # The production default target is now ``static`` (see
        # test_build_models.test_target_defaults_to_static). This helper pins
        # ``angular`` because most tests here assert Angular output; the static
        # tests override it with ``target="static"`` explicitly.
        "target": "angular",
        "guides": [
            {
                "slug": "onboarding",
                "title": "Onboarding",
                "scenario": "onboarding.md",
                "modes": ["stepbystep", "interactive"],
            },
        ],
        "navbar": {"order": ["onboarding"]},
    }
    base.update(overrides)
    return BuildConfig(**base)


def _setup(tmp_path: Path, **overrides) -> tuple[BuildConfig, Path]:
    _write_scenario(tmp_path)
    _write_screenshots(tmp_path)
    return _make_config(tmp_path, **overrides), tmp_path


# ── Angular target ──────────────────────────────────────────────────────────


class TestAngularInteractive:
    def test_emits_manifest_and_real_component(self, tmp_path):
        config, root = _setup(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        assets_dir = root / "output" / "assets"

        # Manifest lives alongside the screenshots in assets_dir, so the host
        # serves it at /assets/<role>/<slug>/guide.json.
        manifest_path = assets_dir / "onboarding" / "guide.json"
        assert manifest_path.exists(), "interactive manifest missing"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["mode"] == "interactive"
        assert manifest["slug"] == "onboarding"
        assert manifest["role"] == "editor"
        # Two steps, one interactive (click) + one non-interactive (navigate)
        assert len(manifest["steps"]) == 2
        assert manifest["steps"][0]["interactive"] is True
        assert manifest["steps"][1]["interactive"] is False

    def test_interactive_component_mounts_steppy_guide(self, tmp_path):
        config, root = _setup(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        component_path = root / "output" / "generated" / "onboarding" / "onboarding-interactive.component.ts"
        assert component_path.exists(), "interactive component missing"
        body = component_path.read_text(encoding="utf-8")
        # Stage 4 replaces the "Coming soon" placeholder with a real mount.
        assert "Coming soon" not in body
        assert "<steppy-guide" in body
        # References the manifest URL the build emits.
        assert "/assets/editor/onboarding/guide.json" in body
        assert "/assets/editor/steppy-player.js" in body
        # Bridges ngx-translate locale into the player.
        assert "TranslateService" in body
        assert "CUSTOM_ELEMENTS_SCHEMA" in body

    def test_copies_player_bundle_to_assets(self, tmp_path):
        from steppy._player_bundle import get_player_bundle_path

        config, root = _setup(tmp_path)
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        bundle_path = root / "output" / "assets" / "steppy-player.js"
        assert bundle_path.exists(), "player bundle not copied to assets_dir"
        # The copied bundle must match the vendored source byte-for-byte.
        assert bundle_path.read_bytes() == get_player_bundle_path().read_bytes()

    def test_no_bundle_copy_when_interactive_off(self, tmp_path):
        config, root = _setup(
            tmp_path,
            guides=[
                {
                    "slug": "onboarding",
                    "title": "Onboarding",
                    "scenario": "onboarding.md",
                    "modes": ["stepbystep"],
                }
            ],
        )
        assembler = BuildAssembler(config, root)
        assembler.build_all()
        bundle_path = root / "output" / "assets" / "steppy-player.js"
        assert not bundle_path.exists(), "player bundle copied even though no guide opts into interactive"


# ── Static target ───────────────────────────────────────────────────────────


class TestStaticInteractive:
    def test_emits_manifest_and_interactive_html(self, tmp_path):
        config, root = _setup(tmp_path, target="static")
        assembler = BuildAssembler(config, root)
        assembler.build_all()

        output_dir = root / "output" / "generated"
        manifest_path = output_dir / "onboarding" / "guide.json"
        page_path = output_dir / "onboarding" / "interactive.html"
        bundle_path = output_dir / "assets" / "steppy-player.js"

        assert manifest_path.exists()
        assert page_path.exists()
        assert bundle_path.exists()

        page = page_path.read_text(encoding="utf-8")
        assert "<steppy-guide" in page
        assert "./guide.json" in page
        assert "../assets/steppy-player.js" in page

    def test_screenshot_urls_are_relative(self, tmp_path):
        config, root = _setup(tmp_path, target="static")
        BuildAssembler(config, root).build_all()
        manifest_path = root / "output" / "generated" / "onboarding" / "guide.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        first_frame = manifest["steps"][0]["captures"]["fr"]["_"]["frames"][0]
        # No layouts configured: synthetic "_" key. URL is relative.
        assert first_frame["screenshot"].startswith("../assets/onboarding/")

    def test_interactive_only_guide_still_writes_stepbystep_neighbors(self, tmp_path):
        # Coexistence: a guide with BOTH modes emits both stepbystep <locale>.html
        # and interactive.html in the same directory.
        config, root = _setup(tmp_path, target="static")
        BuildAssembler(config, root).build_all()
        guide_dir = root / "output" / "generated" / "onboarding"
        assert (guide_dir / "fr.html").exists(), "stepbystep page missing"
        assert (guide_dir / "interactive.html").exists(), "interactive page missing"


# ── Presentation template resolution ────────────────────────────────────────


class TestTemplateResolution:
    def _manifest(self, root: Path) -> dict:
        return json.loads((root / "output" / "generated" / "onboarding" / "guide.json").read_text(encoding="utf-8"))

    def test_defaults_to_overlay(self, tmp_path):
        config, root = _setup(tmp_path, target="static")
        BuildAssembler(config, root).build_all()
        assert self._manifest(root)["defaultTemplate"] == "overlay"
        page = (root / "output" / "generated" / "onboarding" / "interactive.html").read_text(encoding="utf-8")
        assert 'template="overlay"' in page

    def test_project_default_template_applies(self, tmp_path):
        config, root = _setup(tmp_path, target="static", defaultTemplate="inline")
        BuildAssembler(config, root).build_all()
        assert self._manifest(root)["defaultTemplate"] == "inline"

    def test_per_guide_template_overrides_project_default(self, tmp_path):
        config, root = _setup(
            tmp_path,
            target="static",
            defaultTemplate="inline",
            guides=[
                {
                    "slug": "onboarding",
                    "title": "Onboarding",
                    "scenario": "onboarding.md",
                    "modes": ["stepbystep", "interactive"],
                    "template": "sidebar",
                }
            ],
        )
        BuildAssembler(config, root).build_all()
        assert self._manifest(root)["defaultTemplate"] == "sidebar"

    def test_invalid_template_rejected(self):
        with pytest.raises(ValidationError):
            BuildConfig(
                scenariosRoot="./scenarios",
                outputDir="./out",
                assetsDir="./assets",
                role="editor",
                site={"title": "X"},
                guides=[
                    {"slug": "g", "title": "G", "scenario": "g.md"},
                ],
                navbar={"order": ["g"]},
                defaultTemplate="popover",
            )


# ── Cross-target: dry_run + idempotency ─────────────────────────────────────


class TestInteractiveDryRun:
    def test_dry_run_lists_planned_files(self, tmp_path):
        config, root = _setup(tmp_path)
        results = BuildAssembler(config, root).build_all(dry_run=True)

        # Every interactive-mode output should be "planned" without writing
        def rel(p):
            return str(p).replace("\\", "/")

        keys = [rel(k) for k in results.keys()]
        assert any("onboarding/guide.json" in k for k in keys)
        assert any("steppy-player.js" in k for k in keys)
        # Confirm nothing actually landed on disk.
        assert not (root / "output" / "assets" / "steppy-player.js").exists()


# ── cursor mode wiring (project-level + per-guide override) ─────────────────


class TestCursorModeWiring:
    """End-to-end: BuildConfig.cursor + per-guide GuideEntry.cursor reach the
    rendered Angular component, the static interactive.html page, and the
    manifest's ``cursor.mode`` field."""

    def test_default_arrow_omits_attribute_and_manifest_mode(self, tmp_path):
        """Byte-identical default: with cursor unset, the rendered host has no
        ``cursor=`` attribute and the manifest's cursor block has no
        ``mode`` key. This guards against accidentally bloating the default
        output."""
        config, root = _setup(tmp_path)
        BuildAssembler(config, root).build_all()

        component = (root / "output" / "generated" / "onboarding" / "onboarding-interactive.component.ts").read_text(
            encoding="utf-8"
        )
        assert "cursor=" not in component

        manifest = json.loads((root / "output" / "assets" / "onboarding" / "guide.json").read_text(encoding="utf-8"))
        assert "mode" not in manifest["cursor"]

    def test_project_level_hotspot_threads_to_angular_and_manifest(self, tmp_path):
        config, root = _setup(tmp_path, cursor="hotspot")
        BuildAssembler(config, root).build_all()

        component = (root / "output" / "generated" / "onboarding" / "onboarding-interactive.component.ts").read_text(
            encoding="utf-8"
        )
        assert 'cursor="hotspot"' in component

        manifest = json.loads((root / "output" / "assets" / "onboarding" / "guide.json").read_text(encoding="utf-8"))
        assert manifest["cursor"]["mode"] == "hotspot"
        # Timings still present.
        assert "pre_action_ms" in manifest["cursor"]

    def test_project_level_off_threads_to_static_html(self, tmp_path):
        config, root = _setup(tmp_path, target="static", cursor="off")
        BuildAssembler(config, root).build_all()

        page = (root / "output" / "generated" / "onboarding" / "interactive.html").read_text(encoding="utf-8")
        assert 'cursor="off"' in page

        manifest = json.loads((root / "output" / "generated" / "onboarding" / "guide.json").read_text(encoding="utf-8"))
        assert manifest["cursor"]["mode"] == "off"

    def test_per_guide_override_wins_over_project_default(self, tmp_path):
        config, root = _setup(
            tmp_path,
            cursor="off",
            guides=[
                {
                    "slug": "onboarding",
                    "title": "Onboarding",
                    "scenario": "onboarding.md",
                    "modes": ["stepbystep", "interactive"],
                    # Per-guide override beats the project-level "off".
                    "cursor": "hotspot",
                },
            ],
        )
        BuildAssembler(config, root).build_all()

        manifest = json.loads((root / "output" / "assets" / "onboarding" / "guide.json").read_text(encoding="utf-8"))
        assert manifest["cursor"]["mode"] == "hotspot"

        component = (root / "output" / "generated" / "onboarding" / "onboarding-interactive.component.ts").read_text(
            encoding="utf-8"
        )
        assert 'cursor="hotspot"' in component


# ── Interactive + multi-layout (unified host toggle) ────────────────────────


def _setup_layouts(tmp_path: Path, **overrides) -> tuple[BuildConfig, Path]:
    """Like ``_setup`` but captures per-layout screenshots and configures
    ``layouts`` so interactive mode exercises the layout dimension."""
    _write_scenario(tmp_path)
    _write_screenshots(tmp_path, layouts=("desktop", "mobile"))
    cfg = {"layouts": ["desktop", "mobile"], "defaultLayout": "desktop"}
    cfg.update(overrides)
    return _make_config(tmp_path, **cfg), tmp_path


class TestInteractiveWithLayouts:
    """The unified host layout toggle drives interactive mode on both targets;
    the player's built-in switcher is suppressed via ``hide-layout-switcher``."""

    def test_manifest_carries_both_layouts(self, tmp_path):
        config, root = _setup_layouts(tmp_path)
        BuildAssembler(config, root).build_all()
        manifest = json.loads((root / "output" / "assets" / "onboarding" / "guide.json").read_text(encoding="utf-8"))
        assert manifest["layouts"] == ["desktop", "mobile"]
        assert manifest["defaultLayout"] == "desktop"
        caps = manifest["steps"][0]["captures"]["fr"]
        assert set(caps.keys()) == {"desktop", "mobile"}

    def test_twin_branch_scenario_builds_and_carries_per_layout_text(self, tmp_path):
        """End-to-end regression for the twin-branch pattern.

        Complementary ``layouts:`` whitelists used to raise a ``BuildError``
        outright, which failed most of a real project's guides. Now the build
        succeeds and each layout's own wording reaches the manifest.
        """
        scenarios = tmp_path / "scenarios"
        scenarios.mkdir(parents=True, exist_ok=True)
        (scenarios / "onboarding.md").write_text(
            dedent("""\
            ---
            title: Onboarding
            tags: [user-guide-editor]
            guide:
              enabled: true
            ---

            ### 1. Click new
            - role: editor
            - action: click
            - selector: '[data-testid="new"]'
            - screenshot: editor
            - guide-text:
                fr: "Cliquez sur Nouveau"
                nl: "Klik op Nieuw"

            ### 2. Open the row (desktop)
            - role: editor
            - action: click
            - selector: '[data-testid="row"]'
            - screenshot: editor
            - layouts:
                - desktop
            - guide-text:
                fr: "Cliquez sur la ligne du tableau"
                nl: "Klik op de rij"

            ### 3. Open the card (mobile)
            - role: editor
            - action: click
            - selector: '[data-testid="card"]'
            - screenshot: editor
            - layouts:
                - mobile
            - guide-text:
                fr: "Touchez la carte"
                nl: "Klik op de rij"
            """),
            encoding="utf-8",
        )

        bbox = {"x": 100, "y": 200, "width": 50, "height": 30}
        for locale in ("fr", "nl"):
            for layout, indices in (("desktop", (1, 2)), ("mobile", (1, 3))):
                folder = tmp_path / "screenshots" / "onboarding" / locale / layout
                folder.mkdir(parents=True, exist_ok=True)
                for idx in indices:
                    (folder / f"step-{idx:02d}.png").write_bytes(_minimal_png())
                    (folder / f"step-{idx:02d}.json").write_text(
                        json.dumps(
                            {
                                "viewport": {"width": 1280, "height": 720},
                                "target": {"kind": "single", "bbox": bbox, "selector": "x"},
                            }
                        ),
                        encoding="utf-8",
                    )

        config = _make_config(tmp_path, layouts=["desktop", "mobile"], defaultLayout="desktop")
        BuildAssembler(config, tmp_path).build_all()

        manifest = json.loads(
            (tmp_path / "output" / "assets" / "onboarding" / "guide.json").read_text(encoding="utf-8")
        )
        twin = manifest["steps"][1]
        assert twin["captures"]["fr"]["desktop"]["frames"][0]["screenshot"].endswith("step-02.png")
        assert twin["captures"]["fr"]["mobile"]["frames"][0]["screenshot"].endswith("step-03.png")
        assert twin["text"]["fr"] == "Cliquez sur la ligne du tableau"
        assert twin["captures"]["fr"]["mobile"]["content"]["text"] == "Touchez la carte"
        # Dutch reads the same in both layouts, so it inherits.
        assert "content" not in twin["captures"]["nl"]["mobile"]

    def test_angular_host_drives_interactive_variant(self, tmp_path):
        config, root = _setup_layouts(tmp_path)
        BuildAssembler(config, root).build_all()
        gen = root / "output" / "generated" / "onboarding"

        interactive = (gen / "onboarding-interactive.component.ts").read_text(encoding="utf-8")
        # Layout is a signal input bound to the player's variant attribute, and
        # the player's own switcher is suppressed.
        assert "input<string>('desktop')" in interactive
        assert '[attr.variant]="variant()"' in interactive
        assert "hide-layout-switcher" in interactive

        parent = (gen / "onboarding.component.ts").read_text(encoding="utf-8")
        # The shared toolbar's layout toggle feeds the interactive child.
        assert '[variant]="selectedLayout()"' in parent

    def test_static_interactive_page_has_layout_toggle(self, tmp_path):
        config, root = _setup_layouts(tmp_path, target="static")
        BuildAssembler(config, root).build_all()
        page = (root / "output" / "generated" / "onboarding" / "interactive.html").read_text(encoding="utf-8")
        # Page-level layout nav + suppressed in-player switcher + JS that drives
        # the player's `variant` attribute.
        assert 'class="steppy-layout-switch"' in page
        assert "hide-layout-switcher" in page
        assert "setAttribute('variant'" in page
        assert 'data-layout="desktop"' in page
        assert 'data-layout="mobile"' in page

    def test_single_layout_omits_toggle(self, tmp_path):
        # Back-compat: no layouts configured ⇒ no host toggle and no
        # hide-layout-switcher (output shape unchanged from pre-feature).
        config, root = _setup(tmp_path, target="static")
        BuildAssembler(config, root).build_all()
        page = (root / "output" / "generated" / "onboarding" / "interactive.html").read_text(encoding="utf-8")
        assert "hide-layout-switcher" not in page
        assert "steppy-layout-switch" not in page

    def test_explicit_arrow_collapses_to_default(self, tmp_path):
        """An explicit ``cursor: 'arrow'`` is equivalent to leaving it unset —
        no attribute on the host, no ``mode`` key in the manifest. This keeps
        existing fixtures byte-identical when authors flip it on and back."""
        config, root = _setup(tmp_path, cursor="arrow")
        BuildAssembler(config, root).build_all()

        component = (root / "output" / "generated" / "onboarding" / "onboarding-interactive.component.ts").read_text(
            encoding="utf-8"
        )
        assert "cursor=" not in component

        manifest = json.loads((root / "output" / "assets" / "onboarding" / "guide.json").read_text(encoding="utf-8"))
        assert "mode" not in manifest["cursor"]


# ── Progressive fill-form end-to-end through the build ──────────────────────


def _write_progressive_scenario(tmp_path: Path) -> None:
    """One-step fill-form scenario with ``interactive-progressive: true``."""
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir(parents=True, exist_ok=True)
    (scenarios / "signup.md").write_text(
        dedent("""\
            ---
            title: Signup
            tags: [user-guide-editor]
            guide:
              enabled: true
            ---

            ### 1. Fill the signup form
            - role: editor
            - action: fill-form
            - interactive-progressive: true
            - screenshot: editor
            - fields:
                email: "user@example.com"
                password: "secret"
            - guide-text:
                fr: "Remplissez le formulaire"
                nl: "Vul het formulier in"
            """),
        encoding="utf-8",
    )


def _write_progressive_screenshots(
    tmp_path: Path,
    slug: str = "signup",
    locales: tuple[str, ...] = ("fr", "nl"),
) -> None:
    """Write the per-field + settled frames for a 2-field progressive form.

    Field frames carry single-target sidecars (one per field); the settled
    frame carries ``target: null`` so its cursor renders as None.
    """
    base = tmp_path / "screenshots" / slug
    field_specs = [
        ("a", "email", {"x": 10, "y": 20, "width": 200, "height": 30}),
        ("b", "password", {"x": 10, "y": 60, "width": 200, "height": 30}),
    ]
    settled_letter = "c"
    for locale in locales:
        folder = base / locale
        folder.mkdir(parents=True, exist_ok=True)
        # Per-field frames
        for letter, selector, bbox in field_specs:
            stem = f"step-01-{letter}"
            (folder / f"{stem}.png").write_bytes(_minimal_png())
            sidecar = {
                "viewport": {"width": 1280, "height": 720},
                "target": {"kind": "single", "bbox": bbox, "selector": selector},
            }
            (folder / f"{stem}.json").write_text(json.dumps(sidecar), encoding="utf-8")
        # Settled frame: cursor=None
        stem = f"step-01-{settled_letter}"
        (folder / f"{stem}.png").write_bytes(_minimal_png())
        (folder / f"{stem}.json").write_text(
            json.dumps({"viewport": {"width": 1280, "height": 720}, "target": None}),
            encoding="utf-8",
        )


def _make_progressive_config(tmp_path: Path, **overrides) -> BuildConfig:
    base = {
        "scenariosRoot": "./scenarios",
        "screenshotsDir": "./screenshots",
        "outputDir": "./output/generated",
        "assetsDir": "./output/assets",
        "role": "editor",
        "locales": ["fr", "nl"],
        "defaultLocale": "fr",
        "site": {"title": "Demo Guides"},
        # Asserts the Angular manifest path (assets_dir); pin the target.
        "target": "angular",
        "guides": [
            {
                "slug": "signup",
                "title": "Signup",
                "scenario": "signup.md",
                "modes": ["interactive"],
            },
        ],
        "navbar": {"order": ["signup"]},
    }
    base.update(overrides)
    return BuildConfig(**base)


class TestProgressiveFillFormBuild:
    """End-to-end through ``BuildAssembler.build_all`` for a progressive
    fill-form step. The manifest must surface per-field frames with single-
    target ``type`` cursors, plus a settled frame with ``cursor === null``."""

    def test_manifest_has_per_field_frames_plus_settled(self, tmp_path):
        _write_progressive_scenario(tmp_path)
        _write_progressive_screenshots(tmp_path)
        config = _make_progressive_config(tmp_path)
        BuildAssembler(config, tmp_path).build_all()

        manifest = json.loads((tmp_path / "output" / "assets" / "signup" / "guide.json").read_text(encoding="utf-8"))
        assert len(manifest["steps"]) == 1
        step = manifest["steps"][0]
        assert step["action"] == "fill-form"
        # Step is interactive: frame 0 has a non-null cursor.
        assert step["interactive"] is True

        for locale in ("fr", "nl"):
            frames = step["captures"][locale]["_"]["frames"]
            assert len(frames) == 3  # 2 fields + 1 settled

            # email frame
            assert frames[0]["cursor"]["kind"] == "type"
            assert frames[0]["cursor"]["target"]["selector"] == "email"
            assert frames[0]["cursor"]["value"] == "user@example.com"

            # password frame
            assert frames[1]["cursor"]["kind"] == "type"
            assert frames[1]["cursor"]["target"]["selector"] == "password"
            assert frames[1]["cursor"]["value"] == "secret"

            # settled frame
            assert frames[2]["cursor"] is None
