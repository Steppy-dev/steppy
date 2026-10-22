"""Tests for ``GuideAssembler.prepare_interactive_manifest`` and its helpers.

These cover the build-side shape of ``guide.json`` that the Lit player consumes.
The Zod schema in ``packages/player/src/manifest/schema.ts`` is the runtime
contract; these tests verify Python emits manifests that pass it.
"""

import json
import struct
import zlib
from pathlib import Path
from textwrap import dedent
from typing import Optional

import pytest

from steppy.guide import (
    GuideAssembler,
    INTERACTIVE_MANIFEST_VERSION,
    _cursor_has_complete_bboxes,
    _normalize_cursor_for_manifest,
)
from steppy.models import (
    AppAuthConfig,
    AppConfig,
    GlobalAuthConfig,
    LayoutConfig,
    ScreenshotConfig,
    SteppyConfig,
)
from steppy.parser import parse_scenario_file


# ── PNG / sidecar fixtures ───────────────────────────────────────────────────


def _minimal_png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = chunk(b"IDAT", raw)
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _write_single_sidecar(
    folder: Path,
    idx: int,
    bbox: Optional[dict],
    selector: str = "x",
    viewport: Optional[dict] = None,
    frame: Optional[str] = None,
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"step-{idx:02d}" + (f"-{frame}" if frame else "")
    (folder / f"{stem}.png").write_bytes(_minimal_png())
    payload = {
        "viewport": viewport or {"width": 1280, "height": 720},
        "target": {"kind": "single", "selector": selector, "bbox": bbox},
    }
    (folder / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_navigate_sidecar(
    folder: Path,
    idx: int,
    viewport: Optional[dict] = None,
) -> None:
    """A non-visual action (navigate/assert/wait) has no target in its sidecar.

    Mirrors what ``take_screenshot_with_bbox`` writes when the action is in
    ``_HIDDEN_CURSOR_ACTIONS``."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"step-{idx:02d}.png").write_bytes(_minimal_png())
    payload = {
        "viewport": viewport or {"width": 1280, "height": 720},
        "target": None,
    }
    (folder / f"step-{idx:02d}.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_multi_sidecar(
    folder: Path,
    idx: int,
    fields: list[dict],
    viewport: Optional[dict] = None,
    frame: Optional[str] = None,
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"step-{idx:02d}" + (f"-{frame}" if frame else "")
    (folder / f"{stem}.png").write_bytes(_minimal_png())
    payload = {
        "viewport": viewport or {"width": 1280, "height": 720},
        "target": {"kind": "multi", "fields": fields},
    }
    (folder / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")


# ── Config + scenario helpers ────────────────────────────────────────────────


def _make_config() -> SteppyConfig:
    return SteppyConfig(
        apps={
            "editor": AppConfig(
                name="Editor",
                url="http://localhost:4200",
                auth=AppAuthConfig(mode="auth0"),
            )
        },
        auth=GlobalAuthConfig(),
        locales=["fr", "nl"],
        default_locale="fr",
        screenshot=ScreenshotConfig(
            layouts={
                "desktop": LayoutConfig(width=1440, height=900),
                "mobile": LayoutConfig(width=390, height=844),
            },
        ),
    )


def _write_scenario(scenarios_root: Path, body: str) -> Path:
    scenarios_root.mkdir(parents=True, exist_ok=True)
    path = scenarios_root / "onboarding.md"
    path.write_text(body, encoding="utf-8")
    return path


def _basic_scenario() -> str:
    return dedent("""\
        ---
        title: Onboarding
        tags: [user-guide-editor]
        guide:
          enabled: true
        ---

        ### 1. Click the new button
        - role: editor
        - action: click
        - selector: '[data-testid="new"]'
        - screenshot: editor
        - guide-text:
            fr:
              title: Cliquez sur Nouveau
              text: Cliquez sur le bouton Nouveau.
            nl:
              title: Klik op Nieuw
              text: Klik op de Nieuwe knop.

        ### 2. Navigate away
        - role: editor
        - action: navigate
        - target: /next
        - screenshot: editor
        - guide-text:
            fr:
              title: Page suivante
              text: Vous y êtes.
            nl:
              title: Volgende pagina
              text: U bent er.
        """)


# ── _normalize_cursor_for_manifest ───────────────────────────────────────────


class TestNormalizeCursor:
    def test_none_passes_through(self):
        assert _normalize_cursor_for_manifest(None) is None

    def test_click_with_selector_and_bbox(self):
        bbox = {"x": 1, "y": 2, "width": 3, "height": 4}
        out = _normalize_cursor_for_manifest({"kind": "click", "target": {"selector": "#x", "bbox": bbox}})
        assert out == {"kind": "click", "target": {"bbox": bbox, "selector": "#x"}}

    def test_click_drops_none_selector(self):
        bbox = {"x": 1, "y": 2, "width": 3, "height": 4}
        out = _normalize_cursor_for_manifest({"kind": "click", "target": {"selector": None, "bbox": bbox}})
        assert out == {"kind": "click", "target": {"bbox": bbox}}

    def test_custom_drops_extra_value_on_click(self):
        bbox = {"x": 1, "y": 2, "width": 3, "height": 4}
        out = _normalize_cursor_for_manifest(
            {
                "kind": "click",
                "target": {"selector": "#x", "bbox": bbox},
                "value": None,  # _compute_cursor_behavior leaks this for custom steps
            }
        )
        # 'value' must be absent on click (Zod strict mode rejects extras)
        assert "value" not in out
        assert out["kind"] == "click"

    def test_type_keeps_value(self):
        bbox = {"x": 0, "y": 0, "width": 100, "height": 30}
        out = _normalize_cursor_for_manifest({"kind": "type", "target": {"selector": "#n", "bbox": bbox}, "value": "x"})
        assert out == {
            "kind": "type",
            "target": {"bbox": bbox, "selector": "#n"},
            "value": "x",
        }

    def test_type_drops_none_value(self):
        bbox = {"x": 0, "y": 0, "width": 100, "height": 30}
        out = _normalize_cursor_for_manifest({"kind": "type", "target": {"bbox": bbox}, "value": None})
        assert "value" not in out

    def test_type_multi_normalizes_each_target(self):
        bbox_a = {"x": 0, "y": 0, "width": 10, "height": 10}
        bbox_b = {"x": 0, "y": 20, "width": 10, "height": 10}
        out = _normalize_cursor_for_manifest(
            {
                "kind": "type-multi",
                "targets": [
                    {"selector": "#a", "bbox": bbox_a, "value": "1"},
                    {"selector": None, "bbox": bbox_b, "value": None},
                ],
            }
        )
        assert out == {
            "kind": "type-multi",
            "targets": [
                {"bbox": bbox_a, "selector": "#a", "value": "1"},
                {"bbox": bbox_b},
            ],
        }


# ── _cursor_has_complete_bboxes ──────────────────────────────────────────────


class TestCursorBboxes:
    def test_none_is_complete(self):
        assert _cursor_has_complete_bboxes(None) is True

    def test_single_target_missing_bbox(self):
        assert _cursor_has_complete_bboxes({"kind": "click", "target": {"bbox": None}}) is False

    def test_single_target_has_bbox(self):
        bbox = {"x": 1, "y": 2, "width": 3, "height": 4}
        assert _cursor_has_complete_bboxes({"kind": "click", "target": {"bbox": bbox}}) is True

    def test_type_multi_with_all_bboxes(self):
        bbox = {"x": 0, "y": 0, "width": 1, "height": 1}
        assert (
            _cursor_has_complete_bboxes(
                {
                    "kind": "type-multi",
                    "targets": [{"bbox": bbox}, {"bbox": bbox}],
                }
            )
            is True
        )

    def test_type_multi_with_missing_bbox(self):
        bbox = {"x": 0, "y": 0, "width": 1, "height": 1}
        assert (
            _cursor_has_complete_bboxes(
                {
                    "kind": "type-multi",
                    "targets": [{"bbox": bbox}, {"bbox": None}],
                }
            )
            is False
        )


# ── prepare_interactive_manifest — happy path ────────────────────────────────


def _bbox(x: int = 10, y: int = 20, w: int = 50, h: int = 30) -> dict:
    return {"x": x, "y": y, "width": w, "height": h}


def _url_fn(slug, step_index, frame_key, locale, layout):
    stem = f"step-{step_index:02d}" + (f"-{frame_key}" if frame_key else "")
    segs = [locale]
    if layout is not None:
        segs.append(layout)
    return f"./{slug}/{'/'.join(segs)}/{stem}.png"


def _seed_screenshots(
    screenshots_dir: Path,
    slug: str,
    locales: list[str],
    layouts: list[Optional[str]],
    *,
    click_bbox: Optional[dict] = None,
) -> None:
    """Write step-01 (click) and step-02 (navigate) sidecars + PNGs for every
    (locale, layout) combination."""
    bbox = click_bbox if click_bbox is not None else _bbox()
    for locale in locales:
        for layout in layouts:
            folder = screenshots_dir / slug / locale
            if layout is not None:
                folder = folder / layout
            _write_single_sidecar(folder, 1, bbox=bbox, selector='[data-testid="new"]')
            _write_navigate_sidecar(folder, 2)


class TestPrepareInteractiveManifest:
    def test_happy_path_with_layouts(self, tmp_path):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)

        _seed_screenshots(
            screenshots_dir,
            scenario.slug,
            locales=["fr", "nl"],
            layouts=["desktop", "mobile"],
        )

        manifest = assembler.prepare_interactive_manifest(
            scenario=scenario,
            role="editor",
            screenshots_dir=screenshots_dir,
            locales=["fr", "nl"],
            layouts=["desktop", "mobile"],
            default_locale="fr",
            default_layout="desktop",
            title={"fr": "Onboarding", "nl": "Onboarding"},
            cursor_timings={
                "pre_action_ms": 200,
                "action_ms": 300,
                "post_action_ms": 150,
                "travel_speed_px_per_ms": 1.5,
            },
            screenshot_url_fn=_url_fn,
        )

        assert manifest["version"] == INTERACTIVE_MANIFEST_VERSION
        assert manifest["slug"] == scenario.slug
        assert manifest["mode"] == "interactive"
        assert manifest["locales"] == ["fr", "nl"]
        assert manifest["defaultLocale"] == "fr"
        assert manifest["layouts"] == ["desktop", "mobile"]
        assert manifest["defaultLayout"] == "desktop"

        # Two steps: click (interactive) + navigate (not interactive)
        assert len(manifest["steps"]) == 2

        # defaultTemplate defaults to "overlay" when not passed.
        assert manifest["defaultTemplate"] == "overlay"

        click_step = manifest["steps"][0]
        assert click_step["index"] == 1
        assert click_step["id"] == "step-1"
        assert click_step["action"] == "click"
        assert click_step["interactive"] is True
        # Text from guide-text
        assert "Cliquez" in click_step["text"]["fr"]
        # Per-step title is the short heading from guide-text (distinct from text).
        assert click_step["title"]["fr"] == "Cliquez sur Nouveau"
        assert click_step["title"]["nl"] == "Klik op Nieuw"
        # All four (locale, layout) combinations present
        for locale in ("fr", "nl"):
            for layout in ("desktop", "mobile"):
                cap = click_step["captures"][locale][layout]
                assert len(cap["frames"]) == 1
                f = cap["frames"][0]
                assert f["captureSize"] == {"width": 1280, "height": 720}
                assert f["cursor"]["kind"] == "click"
                assert "selector" in f["cursor"]["target"]
                assert f["cursor"]["target"]["bbox"]["width"] == 50

        nav_step = manifest["steps"][1]
        assert nav_step["action"] == "navigate"
        assert nav_step["interactive"] is False
        # Navigate has no cursor in any frame
        for locale in ("fr", "nl"):
            for layout in ("desktop", "mobile"):
                assert nav_step["captures"][locale][layout]["frames"][0]["cursor"] is None

    def test_layoutless_uses_synthetic_underscore_key(self, tmp_path):
        cfg = _make_config()
        # Override: no layouts configured
        cfg.screenshot = ScreenshotConfig(layouts={})
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)

        _seed_screenshots(
            screenshots_dir,
            scenario.slug,
            locales=["fr", "nl"],
            layouts=[None],  # no layout subdir
        )

        manifest = assembler.prepare_interactive_manifest(
            scenario=scenario,
            role="editor",
            screenshots_dir=screenshots_dir,
            locales=["fr", "nl"],
            layouts=[],
            default_locale="fr",
            default_layout=None,
            title={"fr": "Onboarding", "nl": "Onboarding"},
            cursor_timings={
                "pre_action_ms": 200,
                "action_ms": 300,
                "post_action_ms": 150,
                "travel_speed_px_per_ms": 1.5,
            },
            screenshot_url_fn=_url_fn,
        )
        assert manifest["layouts"] == []
        assert manifest["defaultLayout"] is None
        # Synthetic "_" key
        for locale in ("fr", "nl"):
            assert "_" in manifest["steps"][0]["captures"][locale]


# ── prepare_interactive_manifest — validation errors ─────────────────────────


class TestPrepareInteractiveManifestErrors:
    def test_empty_locales_raises(self, tmp_path):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)
        with pytest.raises(ValueError, match="locales must be non-empty"):
            assembler.prepare_interactive_manifest(
                scenario=scenario,
                role="editor",
                screenshots_dir=tmp_path / "screenshots",
                locales=[],
                layouts=[],
                default_locale="fr",
                default_layout=None,
                title={},
                cursor_timings={},
                screenshot_url_fn=_url_fn,
            )

    def test_default_locale_not_in_locales(self, tmp_path):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)
        with pytest.raises(ValueError, match="default_locale 'de'"):
            assembler.prepare_interactive_manifest(
                scenario=scenario,
                role="editor",
                screenshots_dir=tmp_path / "screenshots",
                locales=["fr"],
                layouts=[],
                default_locale="de",
                default_layout=None,
                title={"fr": "x"},
                cursor_timings={},
                screenshot_url_fn=_url_fn,
            )

    def test_default_layout_not_in_layouts(self, tmp_path):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)
        with pytest.raises(ValueError, match="default_layout 'tablet'"):
            assembler.prepare_interactive_manifest(
                scenario=scenario,
                role="editor",
                screenshots_dir=tmp_path / "screenshots",
                locales=["fr"],
                layouts=["desktop", "mobile"],
                default_locale="fr",
                default_layout="tablet",
                title={"fr": "x"},
                cursor_timings={},
                screenshot_url_fn=_url_fn,
            )

    def test_missing_bbox_on_click_raises(self, tmp_path):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)

        # Click step has bbox=None (selector failed at capture). Navigate is fine.
        for locale in ("fr", "nl"):
            for layout in ("desktop", "mobile"):
                folder = screenshots_dir / scenario.slug / locale / layout
                _write_single_sidecar(folder, 1, bbox=None, selector='[data-testid="new"]')
                _write_navigate_sidecar(folder, 2)

        with pytest.raises(ValueError, match="missing a bbox"):
            assembler.prepare_interactive_manifest(
                scenario=scenario,
                role="editor",
                screenshots_dir=screenshots_dir,
                locales=["fr", "nl"],
                layouts=["desktop", "mobile"],
                default_locale="fr",
                default_layout="desktop",
                title={"fr": "x", "nl": "x"},
                cursor_timings={
                    "pre_action_ms": 200,
                    "action_ms": 300,
                    "post_action_ms": 150,
                    "travel_speed_px_per_ms": 1.5,
                },
                screenshot_url_fn=_url_fn,
            )

    def test_title_missing_locale_raises(self, tmp_path):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)

        _seed_screenshots(
            screenshots_dir,
            scenario.slug,
            locales=["fr", "nl"],
            layouts=["desktop", "mobile"],
        )

        with pytest.raises(ValueError, match="title is missing locales"):
            assembler.prepare_interactive_manifest(
                scenario=scenario,
                role="editor",
                screenshots_dir=screenshots_dir,
                locales=["fr", "nl"],
                layouts=["desktop", "mobile"],
                default_locale="fr",
                default_layout="desktop",
                title={"fr": "OK"},  # nl missing
                cursor_timings={
                    "pre_action_ms": 200,
                    "action_ms": 300,
                    "post_action_ms": 150,
                    "travel_speed_px_per_ms": 1.5,
                },
                screenshot_url_fn=_url_fn,
            )

    def test_non_uniform_step_coverage_raises(self, tmp_path):
        """A step with ``layouts: [desktop]`` is dropped from mobile, which
        produces divergent step counts across layouts — incompatible with
        interactive mode."""
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        body = dedent("""\
            ---
            title: Diverge
            tags: [user-guide-editor]
            guide:
              enabled: true
            ---

            ### 1. Click first
            - role: editor
            - action: click
            - selector: '#a'
            - screenshot: editor
            - guide-text:
                fr:
                  title: A
                  text: A
                nl:
                  title: A
                  text: A

            ### 2. Click only on desktop
            - role: editor
            - action: click
            - selector: '#b'
            - screenshot: editor
            - layouts:
                - desktop
            - guide-text:
                fr:
                  title: B
                  text: B
                nl:
                  title: B
                  text: B
            """)
        scenario_path = _write_scenario(scenarios_root, body)
        scenario = parse_scenario_file(scenario_path)

        for locale in ("fr", "nl"):
            for layout in ("desktop", "mobile"):
                folder = screenshots_dir / scenario.slug / locale / layout
                _write_single_sidecar(folder, 1, bbox=_bbox(), selector="#a")
                # Step 2 only present on desktop
                if layout == "desktop":
                    _write_single_sidecar(folder, 2, bbox=_bbox(), selector="#b")

        with pytest.raises(ValueError, match="non-uniform step coverage"):
            assembler.prepare_interactive_manifest(
                scenario=scenario,
                role="editor",
                screenshots_dir=screenshots_dir,
                locales=["fr", "nl"],
                layouts=["desktop", "mobile"],
                default_locale="fr",
                default_layout="desktop",
                title={"fr": "x", "nl": "x"},
                cursor_timings={
                    "pre_action_ms": 200,
                    "action_ms": 300,
                    "post_action_ms": 150,
                    "travel_speed_px_per_ms": 1.5,
                },
                screenshot_url_fn=_url_fn,
            )

    def test_layout_twins_with_divergent_cursor_presence_still_raises(self, tmp_path):
        """Twins that disagree on *interactivity* are still fatal.

        Counts balance (one step per layout) so the count guard passes, but the
        desktop twin is a ``click`` (cursor, waits for the viewer) and the
        mobile twin a ``navigate`` (no cursor, auto-advances). ``interactive``
        is a single layout-invariant field on the manifest step, so one of the
        two layouts would get the wrong playback behaviour. This is the guard
        that carries the real behavioural weight now that index divergence is
        allowed.
        """
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"

        body = dedent("""\
            ---
            title: Divergent cursor
            tags: [user-guide-editor]
            guide:
              enabled: true
            ---

            ### 1. Desktop clicks
            - role: editor
            - action: click
            - selector: '#b'
            - screenshot: editor
            - layouts:
                - desktop
            - guide-text:
                fr:
                  title: S1
                  text: S1
                nl:
                  title: S1
                  text: S1

            ### 2. Mobile navigates
            - role: editor
            - action: navigate
            - target: /next
            - screenshot: editor
            - layouts:
                - mobile
            - guide-text:
                fr:
                  title: S2
                  text: S2
                nl:
                  title: S2
                  text: S2
            """)
        scenario_path = _write_scenario(scenarios_root, body)
        scenario = parse_scenario_file(scenario_path)

        for locale in ("fr", "nl"):
            _write_single_sidecar(screenshots_dir / scenario.slug / locale / "desktop", 1, bbox=_bbox(), selector="#b")
            _write_navigate_sidecar(screenshots_dir / scenario.slug / locale / "mobile", 2)

        with pytest.raises(ValueError, match="cursor presence differs"):
            assembler.prepare_interactive_manifest(
                scenario=scenario,
                role="editor",
                screenshots_dir=screenshots_dir,
                locales=["fr", "nl"],
                layouts=["desktop", "mobile"],
                default_locale="fr",
                default_layout="desktop",
                title={"fr": "x", "nl": "x"},
                cursor_timings={
                    "pre_action_ms": 200,
                    "action_ms": 300,
                    "post_action_ms": 150,
                    "travel_speed_px_per_ms": 1.5,
                },
                screenshot_url_fn=_url_fn,
            )


# ── Layout twin-branch pattern ───────────────────────────────────────────────


def _twin_branch_scenario(
    desktop_text: str = "Cliquez sur la ligne du tableau",
    mobile_text: str = "Touchez la carte",
    mobile_action: str = "click",
    nl_mobile_text: str = "Klik op de rij",
    mobile_fr_title: str = "S2",
) -> str:
    """The real-world shape: one logical step authored once per layout.

    Desktop reaches the target through a table row, mobile through a card, so
    the DOM path — and the instruction wording — differ. Steps 1 and 4 are
    shared; 2 is desktop-only and 3 its mobile twin.
    """
    mobile_target = "- target: /next" if mobile_action == "navigate" else "- selector: '#c'"
    return dedent(f"""\
        ---
        title: Complementary
        tags: [user-guide-editor]
        guide:
          enabled: true
        ---

        ### 1. Shared first
        - role: editor
        - action: click
        - selector: '#a'
        - screenshot: editor
        - guide-text:
            fr:
              title: S1
              text: S1
            nl:
              title: S1
              text: S1

        ### 2. Desktop only
        - role: editor
        - action: click
        - selector: '#b'
        - screenshot: editor
        - layouts:
            - desktop
        - guide-text:
            fr:
              title: S2
              text: {desktop_text}
            nl:
              title: S2
              text: Klik op de rij

        ### 3. Mobile only
        - role: editor
        - action: {mobile_action}
        {mobile_target}
        - screenshot: editor
        - layouts:
            - mobile
        - guide-text:
            fr:
              title: {mobile_fr_title}
              text: {mobile_text}
            nl:
              title: S2
              text: {nl_mobile_text}

        ### 4. Shared last
        - role: editor
        - action: click
        - selector: '#d'
        - screenshot: editor
        - guide-text:
            fr:
              title: S4
              text: S4
            nl:
              title: S4
              text: S4
        """)


def _build_twin_manifest(assembler, scenario, screenshots_dir, **overrides):
    kwargs = dict(
        scenario=scenario,
        role="editor",
        screenshots_dir=screenshots_dir,
        locales=["fr", "nl"],
        layouts=["desktop", "mobile"],
        default_locale="fr",
        default_layout="desktop",
        title={"fr": "x", "nl": "x"},
        cursor_timings={
            "pre_action_ms": 200,
            "action_ms": 300,
            "post_action_ms": 150,
            "travel_speed_px_per_ms": 1.5,
        },
        screenshot_url_fn=_url_fn,
    )
    kwargs.update(overrides)
    return assembler.prepare_interactive_manifest(**kwargs)


def _seed_twin_screenshots(screenshots_dir, scenario, locales=("fr", "nl")):
    """Seed exactly what each layout captures: desktop 1,2,4 — mobile 1,3,4."""
    for locale in locales:
        for layout, indices in (("desktop", (1, 2, 4)), ("mobile", (1, 3, 4))):
            folder = screenshots_dir / scenario.slug / locale / layout
            for i in indices:
                _write_single_sidecar(folder, i, bbox=_bbox(), selector=f"#{i}")


class TestLayoutTwinBranch:
    """Complementary ``layouts:`` whitelists across layouts.

    Steps 2 (desktop) and 3 (mobile) are twins, so the filtered lists are
    [1,2,4] and [1,3,4] — equal counts, divergent indices. The manifest joins by
    list *position*, which is what makes the pattern expressible at all: each
    layout contributes its own screenshot to the same manifest step.
    """

    def test_complementary_layouts_build(self, tmp_path):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenario_path = _write_scenario(tmp_path / "scenarios", _twin_branch_scenario())
        scenario = parse_scenario_file(scenario_path)
        screenshots_dir = tmp_path / "screenshots"
        _seed_twin_screenshots(screenshots_dir, scenario)

        manifest = _build_twin_manifest(assembler, scenario, screenshots_dir)

        assert len(manifest["steps"]) == 3
        twin = manifest["steps"][1]
        # Each layout contributes its own screenshot to the same manifest step.
        assert twin["captures"]["fr"]["desktop"]["frames"][0]["screenshot"].endswith("step-02.png")
        assert twin["captures"]["fr"]["mobile"]["frames"][0]["screenshot"].endswith("step-03.png")
        # Step-level text is the canonical (default) layout's.
        assert twin["text"]["fr"] == "Cliquez sur la ligne du tableau"
        # …and mobile carries its own wording beside its own screenshot.
        assert twin["captures"]["fr"]["mobile"]["content"] == {
            "title": "S2",
            "text": "Touchez la carte",
            "subtitle": None,
        }
        # The canonical layout never overrides itself.
        assert "content" not in twin["captures"]["fr"]["desktop"]

    def test_shared_steps_carry_no_content_override(self, tmp_path):
        """Only the twins diverge; steps 1 and 4 stay byte-identical."""
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenario_path = _write_scenario(tmp_path / "scenarios", _twin_branch_scenario())
        scenario = parse_scenario_file(scenario_path)
        screenshots_dir = tmp_path / "screenshots"
        _seed_twin_screenshots(screenshots_dir, scenario)

        manifest = _build_twin_manifest(assembler, scenario, screenshots_dir)

        for position in (0, 2):
            for locale in ("fr", "nl"):
                for layout in ("desktop", "mobile"):
                    assert "content" not in manifest["steps"][position]["captures"][locale][layout]

    def test_content_override_is_per_locale(self, tmp_path):
        """A twin translated identically in one locale overrides only the other.

        The comparison runs per (locale, layout) cell, so `nl` — where both
        twins read "Klik op de rij" — inherits the step level while `fr`
        overrides. Presence of `content` is therefore not uniform across
        locales, and nothing may assume it is.
        """
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenario_path = _write_scenario(tmp_path / "scenarios", _twin_branch_scenario())
        scenario = parse_scenario_file(scenario_path)
        screenshots_dir = tmp_path / "screenshots"
        _seed_twin_screenshots(screenshots_dir, scenario)

        twin = _build_twin_manifest(assembler, scenario, screenshots_dir)["steps"][1]

        assert "content" in twin["captures"]["fr"]["mobile"]
        assert "content" not in twin["captures"]["nl"]["mobile"]

    def test_divergent_title_and_subtitle_override_together(self, tmp_path):
        """The override is atomic — a title-only divergence carries all three.

        Otherwise a partial override would pair one layout's heading with
        another layout's body in the sidebar template.
        """
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        body = _twin_branch_scenario(mobile_fr_title="Ouvrir le menu")
        scenario_path = _write_scenario(tmp_path / "scenarios", body)
        scenario = parse_scenario_file(scenario_path)
        screenshots_dir = tmp_path / "screenshots"
        _seed_twin_screenshots(screenshots_dir, scenario)

        twin = _build_twin_manifest(assembler, scenario, screenshots_dir)["steps"][1]

        assert twin["captures"]["fr"]["mobile"]["content"] == {
            "title": "Ouvrir le menu",
            "text": "Touchez la carte",
            "subtitle": None,
        }
        assert twin["title"]["fr"] == "S2"

    def test_twins_with_different_actions_warn_but_build(self, tmp_path, capsys):
        """Divergent actions are a pairing smell, not a build failure.

        ``action`` rides only the ``steppy:step`` event payload, so a wrong
        value costs an event field, not a wrong render. Raising here would
        recreate the build breakage that over-strict cross-layout validation
        caused in the first place.
        """
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        # The mobile twin hovers instead of clicking. Both actions are
        # cursor-bearing, so the fatal cursor-presence guard stays quiet and the
        # action divergence is the only signal.
        scenario_path = _write_scenario(tmp_path / "scenarios", _twin_branch_scenario(mobile_action="hover"))
        scenario = parse_scenario_file(scenario_path)
        screenshots_dir = tmp_path / "screenshots"
        _seed_twin_screenshots(screenshots_dir, scenario)

        manifest = _build_twin_manifest(assembler, scenario, screenshots_dir)

        assert len(manifest["steps"]) == 3
        warning = capsys.readouterr().err
        assert "layouts disagree on action" in warning
        # The message must locate the pair in the scenario source.
        assert "'desktop': 2" in warning and "'mobile': 3" in warning

    def test_single_layout_build_is_unaffected(self, tmp_path):
        """A one-layout build of the same scenario keeps only its own branch."""
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenario_path = _write_scenario(tmp_path / "scenarios", _twin_branch_scenario())
        scenario = parse_scenario_file(scenario_path)
        screenshots_dir = tmp_path / "screenshots"
        _seed_twin_screenshots(screenshots_dir, scenario)

        manifest = _build_twin_manifest(
            assembler,
            scenario,
            screenshots_dir,
            locales=["fr"],
            layouts=["mobile"],
            default_locale="fr",
            default_layout="mobile",
            title={"fr": "x"},
        )

        assert len(manifest["steps"]) == 3
        twin = manifest["steps"][1]
        # Canonical IS mobile here, so the mobile wording ships at step level
        # and nothing needs overriding. This is what keeps single-layout video
        # manifests free of `content` — and therefore keeps `job_fingerprint`
        # (which hashes the whole manifest) from invalidating every cached
        # render.
        assert twin["text"]["fr"] == "Touchez la carte"
        assert twin["captures"]["fr"]["mobile"]["frames"][0]["screenshot"].endswith("step-03.png")
        assert "content" not in json.dumps(manifest)


# ── Output shape matches Zod schema expectations ─────────────────────────────


class TestManifestShape:
    def test_manifest_is_json_serializable(self, tmp_path):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)
        _seed_screenshots(
            screenshots_dir,
            scenario.slug,
            locales=["fr", "nl"],
            layouts=["desktop", "mobile"],
        )
        manifest = assembler.prepare_interactive_manifest(
            scenario=scenario,
            role="editor",
            screenshots_dir=screenshots_dir,
            locales=["fr", "nl"],
            layouts=["desktop", "mobile"],
            default_locale="fr",
            default_layout="desktop",
            title={"fr": "Onboarding", "nl": "Onboarding"},
            cursor_timings={
                "pre_action_ms": 200,
                "action_ms": 300,
                "post_action_ms": 150,
                "travel_speed_px_per_ms": 1.5,
            },
            screenshot_url_fn=_url_fn,
        )
        # Round-trip through JSON to ensure no non-serializable types leak through
        roundtripped = json.loads(json.dumps(manifest))
        assert roundtripped["slug"] == scenario.slug

    def test_screenshot_urls_use_provided_fn(self, tmp_path):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)
        _seed_screenshots(
            screenshots_dir,
            scenario.slug,
            locales=["fr"],
            layouts=["desktop"],
        )

        def custom_url(slug, step_index, frame_key, locale, layout):
            return f"https://cdn.example.com/{slug}/{locale}/{layout}/step-{step_index:02d}.png"

        manifest = assembler.prepare_interactive_manifest(
            scenario=scenario,
            role="editor",
            screenshots_dir=screenshots_dir,
            locales=["fr"],
            layouts=["desktop"],
            default_locale="fr",
            default_layout="desktop",
            title={"fr": "Onboarding"},
            cursor_timings={
                "pre_action_ms": 200,
                "action_ms": 300,
                "post_action_ms": 150,
                "travel_speed_px_per_ms": 1.5,
            },
            screenshot_url_fn=custom_url,
        )
        frame = manifest["steps"][0]["captures"]["fr"]["desktop"]["frames"][0]
        assert frame["screenshot"].startswith("https://cdn.example.com/")


class TestCursorModeInManifest:
    """``cursor.mode`` round-trip: present when provided, absent by default."""

    def _build(self, tmp_path: Path, cursor_mode):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)
        _seed_screenshots(
            screenshots_dir,
            scenario.slug,
            locales=["fr"],
            layouts=["desktop"],
        )
        kwargs = dict(
            scenario=scenario,
            role="editor",
            screenshots_dir=screenshots_dir,
            locales=["fr"],
            layouts=["desktop"],
            default_locale="fr",
            default_layout="desktop",
            title={"fr": "Onboarding"},
            cursor_timings={
                "pre_action_ms": 200,
                "action_ms": 300,
                "post_action_ms": 150,
                "travel_speed_px_per_ms": 1.5,
            },
            screenshot_url_fn=_url_fn,
        )
        if cursor_mode is not None:
            kwargs["cursor_mode"] = cursor_mode
        return assembler.prepare_interactive_manifest(**kwargs)

    def test_no_cursor_mode_omits_field(self, tmp_path):
        """Default call (no cursor_mode kwarg) preserves byte-identical output —
        the manifest's cursor block carries only timings, no ``mode`` key."""
        manifest = self._build(tmp_path, cursor_mode=None)
        assert "mode" not in manifest["cursor"]
        assert manifest["cursor"]["pre_action_ms"] == 200

    @pytest.mark.parametrize("mode", ["arrow", "hotspot", "off"])
    def test_cursor_mode_emitted(self, tmp_path, mode):
        manifest = self._build(tmp_path, cursor_mode=mode)
        assert manifest["cursor"]["mode"] == mode
        # Timings still present alongside the mode.
        assert manifest["cursor"]["pre_action_ms"] == 200


class TestDefaultTemplateInManifest:
    """``defaultTemplate`` round-trip: 'overlay' by default, else the passed value."""

    def _build(self, tmp_path: Path, default_template):
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _basic_scenario())
        scenario = parse_scenario_file(scenario_path)
        _seed_screenshots(
            screenshots_dir,
            scenario.slug,
            locales=["fr"],
            layouts=["desktop"],
        )
        kwargs = dict(
            scenario=scenario,
            role="editor",
            screenshots_dir=screenshots_dir,
            locales=["fr"],
            layouts=["desktop"],
            default_locale="fr",
            default_layout="desktop",
            title={"fr": "Onboarding"},
            cursor_timings={
                "pre_action_ms": 200,
                "action_ms": 300,
                "post_action_ms": 150,
                "travel_speed_px_per_ms": 1.5,
            },
            screenshot_url_fn=_url_fn,
        )
        if default_template is not None:
            kwargs["default_template"] = default_template
        return assembler.prepare_interactive_manifest(**kwargs)

    def test_defaults_to_overlay(self, tmp_path):
        manifest = self._build(tmp_path, default_template=None)
        assert manifest["defaultTemplate"] == "overlay"

    @pytest.mark.parametrize("template", ["sidebar", "overlay", "inline"])
    def test_template_emitted(self, tmp_path, template):
        manifest = self._build(tmp_path, default_template=template)
        assert manifest["defaultTemplate"] == template


# ── prepare_interactive_manifest — progressive fill-form ─────────────────────


def _progressive_scenario() -> str:
    """A one-step fill-form scenario opting into per-field progressive capture."""
    return dedent("""\
        ---
        title: Progressive Onboarding
        tags: [user-guide-editor]
        guide:
          enabled: true
        ---

        ### 1. Fill the login form
        - role: editor
        - action: fill-form
        - interactive-progressive: true
        - screenshot: editor
        - fields:
            email: "user@example.com"
            password: "secret"
            remember: "on"
        - guide-text:
            fr:
              title: Remplir le formulaire
              text: Remplissez chaque champ.
            nl:
              title: Vul het formulier in
              text: Vul elk veld in.
        """)


def _seed_progressive_screenshots(
    screenshots_dir: Path,
    slug: str,
    locales: list[str],
    layouts: list[Optional[str]],
    *,
    field_bboxes: Optional[dict[str, dict]] = None,
    drop_field: Optional[str] = None,
) -> None:
    """Write the N+1 per-frame sidecars for a 3-field progressive fill-form step.

    ``drop_field``, when set, omits the bbox from that field's sidecar — used to
    exercise the bbox-completeness check.
    """
    field_bboxes = field_bboxes or {
        "email": _bbox(10, 20, 200, 30),
        "password": _bbox(10, 60, 200, 30),
        "remember": _bbox(10, 100, 20, 20),
    }
    selectors = ["email", "password", "remember"]
    letters = ["a", "b", "c"]  # one per field
    settled_letter = "d"
    for locale in locales:
        for layout in layouts:
            folder = screenshots_dir / slug / locale
            if layout is not None:
                folder = folder / layout
            for fk, sel in zip(letters, selectors):
                bbox = None if sel == drop_field else field_bboxes[sel]
                _write_single_sidecar(folder, 1, bbox=bbox, selector=sel, frame=fk)
            # Settled frame: target null → cursor stays hidden.
            folder.mkdir(parents=True, exist_ok=True)
            stem = f"step-01-{settled_letter}"
            (folder / f"{stem}.png").write_bytes(_minimal_png())
            payload = {"viewport": {"width": 1280, "height": 720}, "target": None}
            (folder / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")


class TestPrepareInteractiveManifestProgressive:
    def test_progressive_emits_per_field_frames_with_settled_null(self, tmp_path):
        """A 3-field progressive fill-form emits 4 frames: three with
        single-target ``type`` cursors (selector + bbox + value), then one
        settled frame with ``cursor === null``. The step is marked
        interactive because frame 0 has a cursor."""
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _progressive_scenario())
        scenario = parse_scenario_file(scenario_path)

        _seed_progressive_screenshots(
            screenshots_dir,
            scenario.slug,
            locales=["fr", "nl"],
            layouts=["desktop", "mobile"],
        )

        manifest = assembler.prepare_interactive_manifest(
            scenario=scenario,
            role="editor",
            screenshots_dir=screenshots_dir,
            locales=["fr", "nl"],
            layouts=["desktop", "mobile"],
            default_locale="fr",
            default_layout="desktop",
            title={"fr": "Progressive Onboarding", "nl": "Progressive Onboarding"},
            cursor_timings={
                "pre_action_ms": 200,
                "action_ms": 300,
                "post_action_ms": 150,
                "travel_speed_px_per_ms": 1.5,
            },
            screenshot_url_fn=_url_fn,
        )

        assert len(manifest["steps"]) == 1
        step = manifest["steps"][0]
        assert step["action"] == "fill-form"
        # Step is interactive because frame 0 carries a (non-null) cursor.
        assert step["interactive"] is True

        for locale in ("fr", "nl"):
            for layout in ("desktop", "mobile"):
                frames = step["captures"][locale][layout]["frames"]
                # 3 field frames + 1 settled frame
                assert len(frames) == 4

                # Field frames: single-target type cursor, value pulled from
                # step.fields by selector.
                expected = [
                    ("email", "user@example.com"),
                    ("password", "secret"),
                    ("remember", "on"),
                ]
                for i, (sel, value) in enumerate(expected):
                    cursor = frames[i]["cursor"]
                    assert cursor is not None, f"frame {i} ({locale}/{layout}) missing cursor"
                    assert cursor["kind"] == "type"
                    assert cursor["target"]["selector"] == sel
                    assert cursor["target"]["bbox"] is not None
                    assert cursor["value"] == value

                # Settled frame: cursor is None (player auto-advances).
                assert frames[3]["cursor"] is None

    def test_progressive_missing_bbox_raises(self, tmp_path):
        """If any per-field sidecar is missing its bbox, the bbox-completeness
        check fires — same guarantee as for non-progressive interactive steps."""
        cfg = _make_config()
        assembler = GuideAssembler(cfg)
        scenarios_root = tmp_path / "scenarios"
        screenshots_dir = tmp_path / "screenshots"
        scenario_path = _write_scenario(scenarios_root, _progressive_scenario())
        scenario = parse_scenario_file(scenario_path)

        _seed_progressive_screenshots(
            screenshots_dir,
            scenario.slug,
            locales=["fr"],
            layouts=["desktop"],
            drop_field="password",
        )

        with pytest.raises(ValueError, match="missing a bbox"):
            assembler.prepare_interactive_manifest(
                scenario=scenario,
                role="editor",
                screenshots_dir=screenshots_dir,
                locales=["fr"],
                layouts=["desktop"],
                default_locale="fr",
                default_layout="desktop",
                title={"fr": "Progressive Onboarding"},
                cursor_timings={
                    "pre_action_ms": 200,
                    "action_ms": 300,
                    "post_action_ms": 150,
                    "travel_speed_px_per_ms": 1.5,
                },
                screenshot_url_fn=_url_fn,
            )
