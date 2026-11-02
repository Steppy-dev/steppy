"""Reader language / layout detection (``autoDetect``) on the build side.

The rules themselves (locale matching, precedence, crawler skip, storage
failure, layout from viewport width) are pure TypeScript and are tested in
``packages/player/test/detect.test.ts``. What is pinned here is what the build
hands them: the config model, the breakpoints derived from config.yaml, and the
per-page JSON block every static page carries through the one-line include.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from steppy.build import BuildAssembler
from steppy.build_models import BuildConfig
from steppy.models import SteppyConfig

from tests.test_build_fixture import FIXTURE_ROOT, _make_layout_screenshot_tree

_CONFIG_BLOCK = re.compile(r'<script type="application/json" id="steppy-detect-config">(.*?)</script>', re.DOTALL)


def _base(**overrides) -> dict:
    data = {
        "scenariosRoot": "./scenarios",
        "outputDir": "./out",
        "assetsDir": "./assets",
        "role": "editor",
        "locales": ["fr", "nl"],
        "defaultLocale": "fr",
        "layouts": ["desktop", "mobile"],
        "site": {"title": "Guides"},
        "guides": [{"slug": "onboarding", "title": "Onboarding", "scenario": "editor/onboarding.md"}],
        "navbar": {"order": ["onboarding"]},
    }
    data.update(overrides)
    return data


def _steppy_config(layouts: dict[str, int]) -> SteppyConfig:
    return SteppyConfig(
        apps={"editor": {"name": "E", "url": "http://localhost:3000", "auth": {"mode": "bypass"}}},
        locales=["fr", "nl"],
        default_locale="fr",
        screenshot={"layouts": {name: {"width": w, "height": 800} for name, w in layouts.items()}},
    )


def _detect_config(html: str) -> dict | None:
    match = _CONFIG_BLOCK.search(html)
    return json.loads(match.group(1)) if match else None


# ── Config model ─────────────────────────────────────────────────────────────


class TestAutoDetectConfig:
    def test_defaults_layout_on_locale_off(self):
        auto = BuildConfig(**_base()).auto_detect
        assert auto.layout is True
        assert auto.locale is False
        assert auto.storage_keys.locale == "steppy:locale"
        assert auto.storage_keys.layout == "steppy:layout"
        assert auto.layout_breakpoints is None

    def test_camel_case_keys_parse(self):
        auto = BuildConfig(
            **_base(
                autoDetect={
                    "locale": True,
                    "storageKeys": {"locale": "oce-lang"},
                    "layoutBreakpoints": {"mobile": 0, "desktop": 768},
                }
            )
        ).auto_detect
        assert auto.locale is True
        assert auto.storage_keys.locale == "oce-lang"
        assert auto.storage_keys.layout == "steppy:layout"
        assert auto.layout_breakpoints == {"mobile": 0, "desktop": 768}

    @pytest.mark.parametrize(
        "block",
        [
            {"locales": True},
            {"storageKeys": {"lang": "x"}},
            {"storageKeys": {"locale": ""}},
            {"storageKeys": {"locale": "has space"}},
            {"layoutBreakpoints": {"mobile": -1, "desktop": 768}},
            {"layoutBreakpoints": {"mobile": True, "desktop": 768}},
            {"layoutBreakpoints": {"mobile": 0, "tablet": 600, "desktop": 768}},
            {"layoutBreakpoints": {"desktop": 768}},
        ],
        ids=[
            "unknown-key",
            "unknown-storage-key",
            "empty-key",
            "space-in-key",
            "negative",
            "bool",
            "unknown-layout",
            "missing-layout",
        ],
    )
    def test_rejects(self, block):
        with pytest.raises(ValidationError):
            BuildConfig(**_base(autoDetect=block))


# ── Breakpoints ──────────────────────────────────────────────────────────────


class TestBreakpoints:
    def test_midpoint_between_capture_widths(self, tmp_path):
        assembler = BuildAssembler(
            BuildConfig(**_base()), tmp_path, steppy_config=_steppy_config({"desktop": 1280, "mobile": 390})
        )
        assert assembler._layout_breakpoints == {"mobile": 0, "desktop": 835}

    def test_three_layouts(self, tmp_path):
        assembler = BuildAssembler(
            BuildConfig(**_base(layouts=["desktop", "tablet", "mobile"])),
            tmp_path,
            steppy_config=_steppy_config({"desktop": 1440, "tablet": 768, "mobile": 390}),
        )
        assert assembler._layout_breakpoints == {"mobile": 0, "tablet": 579, "desktop": 1104}
        # A guide carrying only the two wide layouts: its narrowest covers everything below.
        assert assembler._guide_breakpoints(["desktop", "tablet"]) == {"tablet": 0, "desktop": 525}

    def test_explicit_breakpoints_win(self, tmp_path):
        config = BuildConfig(**_base(autoDetect={"layoutBreakpoints": {"mobile": 0, "desktop": 600}}))
        assembler = BuildAssembler(config, tmp_path, steppy_config=_steppy_config({"desktop": 1280, "mobile": 390}))
        assert assembler._layout_breakpoints == {"desktop": 600, "mobile": 0}

    def test_missing_widths_turn_viewport_detection_off(self, tmp_path, capsys):
        assembler = BuildAssembler(BuildConfig(**_base()), tmp_path, steppy_config=None)
        assert assembler._layout_breakpoints is None
        assert "autoDetect.layout is on" in capsys.readouterr().err

    def test_layout_off_needs_no_widths(self, tmp_path, capsys):
        assembler = BuildAssembler(BuildConfig(**_base(autoDetect={"layout": False})), tmp_path)
        assert assembler._layout_breakpoints is None
        assert capsys.readouterr().err == ""


# ── Static pages ─────────────────────────────────────────────────────────────


def _static_config(tmp_path: Path, **overrides) -> BuildConfig:
    screenshots = tmp_path / "screenshots"
    _make_layout_screenshot_tree(screenshots, "onboarding", ["fr", "nl"], ["desktop", "mobile"])
    data = _base(
        scenariosRoot=str(FIXTURE_ROOT / "e2e" / "scenarios"),
        screenshotsDir=str(screenshots),
        outputDir=str(tmp_path / "site"),
        assetsDir=str(tmp_path / "unused"),
        target="static",
        guides=[
            {
                "slug": "onboarding",
                "title": "Onboarding",
                "scenario": "editor/onboarding.md",
                "modes": ["stepbystep", "interactive"],
            }
        ],
        autoDetect={"locale": True, "storageKeys": {"locale": "oce-lang"}},
    )
    data.update(overrides)
    return BuildConfig(**data)


@pytest.fixture
def site(tmp_path) -> Path:
    BuildAssembler(
        _static_config(tmp_path), tmp_path, steppy_config=_steppy_config({"desktop": 1280, "mobile": 390})
    ).build_all()
    return tmp_path / "site"


class TestStaticPages:
    def test_guide_page_carries_locale_and_layout(self, site):
        html = (site / "onboarding" / "nl.html").read_text(encoding="utf-8")
        cfg = _detect_config(html)
        assert cfg == {
            "locale": True,
            "layout": True,
            "pageLocale": "nl",
            "locales": ["fr", "nl"],
            "defaultLocale": "fr",
            "hrefFor": {"fr": "./fr.html", "nl": "./nl.html"},
            "layouts": ["desktop", "mobile"],
            "defaultLayout": "desktop",
            "breakpoints": {"desktop": 835, "mobile": 0},
            "storageKeys": {"locale": "oce-lang", "layout": "steppy:layout"},
        }
        assert '<script src="../assets/steppy-detect.js"></script>' in html
        # Loaded in <head>, so a redirect or layout switch happens before the first paint.
        assert html.index("steppy-detect.js") < html.index("</head>")
        assert 'data-steppy-locale="fr"' in html

    def test_no_js_page_is_unchanged_for_crawlers(self, site):
        html = (site / "onboarding" / "nl.html").read_text(encoding="utf-8")
        assert 'class="steppy-layout steppy-layout-active" data-layout="desktop"' in html
        assert "steppy:active-layout" not in html

    def test_index_pages_link_their_siblings(self, site):
        root = _detect_config((site / "index.html").read_text(encoding="utf-8"))
        assert root["pageLocale"] == "fr"
        assert root["hrefFor"] == {"fr": "./index.html", "nl": "./index.nl.html"}
        assert root["layouts"] == []
        assert '<script src="assets/steppy-detect.js"></script>' in (site / "index.nl.html").read_text(encoding="utf-8")

    def test_interactive_page_sets_the_player_instead_of_redirecting(self, site):
        cfg = _detect_config((site / "onboarding" / "interactive.html").read_text(encoding="utf-8"))
        assert cfg["pageLocale"] is None
        assert cfg["hrefFor"] == {}
        assert cfg["layouts"] == ["desktop", "mobile"]

    def test_script_is_written(self, site):
        script = (site / "assets" / "steppy-detect.js").read_text(encoding="utf-8")
        assert "steppy-detect-config" in script

    def test_single_locale_single_layout_page_has_nothing_to_detect(self, tmp_path):
        config = _static_config(tmp_path, locales=["fr"], layouts=["desktop"])
        BuildAssembler(config, tmp_path).build_all()
        html = (tmp_path / "site" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert "steppy-detect" not in html

    def test_override_without_the_include_keeps_its_own_behaviour(self, tmp_path):
        override = tmp_path / "templates" / "static" / "page.html.j2"
        override.parent.mkdir(parents=True)
        override.write_text("<html><head></head><body>{{ locale }} custom</body></html>", encoding="utf-8")
        BuildAssembler(_static_config(tmp_path), tmp_path).build_all()
        html = (tmp_path / "site" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert html == "<html><head></head><body>fr custom</body></html>"
        # The shared pages still use the bundled templates, and the script ships.
        assert (tmp_path / "site" / "assets" / "steppy-detect.js").is_file()

    def test_override_pulls_it_in_with_one_line(self, tmp_path):
        override = tmp_path / "templates" / "static" / "page.html.j2"
        override.parent.mkdir(parents=True)
        override.write_text(
            '<html><head>{% include "static/_steppy_detect.html.j2" %}</head><body></body></html>', encoding="utf-8"
        )
        BuildAssembler(_static_config(tmp_path), tmp_path).build_all()
        html = (tmp_path / "site" / "onboarding" / "fr.html").read_text(encoding="utf-8")
        assert _detect_config(html)["pageLocale"] == "fr"
