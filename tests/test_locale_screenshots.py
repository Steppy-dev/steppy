"""Per-locale screenshot capture and build behavior."""

import json
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.build import BuildAssembler
from steppy.build_models import BuildConfig
from steppy.models import AppConfig, AppAuthConfig, AppI18nConfig, LocalStorageI18nConfig


# ── i18n config models ─────────────────────────────────────────────────────


class TestAppI18nConfig:
    def test_localstorage_required(self):
        cfg = LocalStorageI18nConfig(key="lang", values={"fr": "fr_BE", "nl": "nl_BE"})
        assert cfg.key == "lang"
        assert cfg.values["fr"] == "fr_BE"

    def test_values_must_be_non_empty(self):
        with pytest.raises(ValueError):
            LocalStorageI18nConfig(key="lang", values={})

    def test_extra_fields_forbidden_localstorage(self):
        with pytest.raises(ValueError):
            LocalStorageI18nConfig(key="lang", values={"fr": "fr_BE"}, extra="boom")

    def test_extra_fields_forbidden_i18n(self):
        with pytest.raises(ValueError):
            AppI18nConfig(
                localstorage=LocalStorageI18nConfig(key="lang", values={"fr": "fr"}),
                extra="boom",
            )

    def test_app_config_accepts_optional_i18n(self):
        # No i18n block — fine.
        app = AppConfig(
            name="Editor",
            url="http://localhost:4200",
            auth=AppAuthConfig(mode="auth0"),
        )
        assert app.i18n is None

        # With i18n block — propagates.
        app = AppConfig(
            name="Editor",
            url="http://localhost:4200",
            auth=AppAuthConfig(mode="auth0"),
            i18n=AppI18nConfig(
                localstorage=LocalStorageI18nConfig(
                    key="user-language",
                    values={"fr": "fr_BE", "nl": "nl_BE"},
                ),
            ),
        )
        assert app.i18n is not None
        assert app.i18n.localstorage.values["fr"] == "fr_BE"


# ── Build with per-locale screenshots ──────────────────────────────────────


def _minimal_png() -> bytes:
    import struct
    import zlib

    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = chunk(b"IDAT", raw)
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _write_step(folder: Path, idx: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"step-{idx:02d}.png").write_bytes(_minimal_png())
    (folder / f"step-{idx:02d}.json").write_text(
        json.dumps(
            {
                "viewport": {"width": 1280, "height": 720},
                "target": {"kind": "single", "bbox": {"x": 0, "y": 0, "width": 10, "height": 10}},
            }
        ),
        encoding="utf-8",
    )


def _make_scenario(tmp_path: Path) -> Path:
    scenarios_root = tmp_path / "scenarios"
    scenarios_root.mkdir(parents=True, exist_ok=True)
    path = scenarios_root / "onboarding.md"
    path.write_text(
        dedent("""\
        ---
        title: Onboarding
        tags:
          - user-guide-editor
        guide:
          enabled: true
        ---

        ### 1. Navigate
        - role: editor
        - action: navigate
        - target: /dashboard
        - screenshot: editor
        - guide-text:
            fr: "Bienvenue"
            nl: "Welkom"

        ### 2. Click
        - role: editor
        - action: click
        - selector: button#go
        - screenshot: editor
        - guide-text:
            fr: "Cliquez"
            nl: "Klik"
        """),
        encoding="utf-8",
    )
    return scenarios_root


def _build_config(tmp_path: Path, target: str = "static") -> BuildConfig:
    return BuildConfig(
        scenariosRoot="./scenarios",
        screenshotsDir="./screenshots",
        outputDir="./output/dist",
        assetsDir="./output/assets",
        role="editor",
        locales=["fr", "nl"],
        defaultLocale="fr",
        target=target,
        site={"title": "Test"},
        guides=[{"slug": "onboarding", "title": "Onboarding", "scenario": "onboarding.md"}],
        navbar={"order": ["onboarding"]},
    )


class TestPerLocaleScreenshotsStatic:
    def test_per_locale_dirs_use_locale_segment(self, tmp_path):
        _make_scenario(tmp_path)
        for loc in ("fr", "nl"):
            _write_step(tmp_path / "screenshots" / "onboarding" / loc, 1)
            _write_step(tmp_path / "screenshots" / "onboarding" / loc, 2)

        cfg = _build_config(tmp_path, target="static")
        BuildAssembler(cfg, tmp_path).build_all()

        out_dir = tmp_path / "output" / "dist"
        fr_html = (out_dir / "onboarding" / "fr.html").read_text(encoding="utf-8")
        nl_html = (out_dir / "onboarding" / "nl.html").read_text(encoding="utf-8")

        assert "../assets/onboarding/fr/step-01.png" in fr_html
        assert "../assets/onboarding/nl/step-01.png" in nl_html
        assert "../assets/onboarding/fr/" not in nl_html
        assert "../assets/onboarding/nl/" not in fr_html

        # Screenshots are copied into per-locale subdirs of the assets folder.
        assets = out_dir / "assets" / "onboarding"
        assert (assets / "fr" / "step-01.png").exists()
        assert (assets / "nl" / "step-01.png").exists()
        # Bbox sidecars accompany each PNG in the same per-locale subdir.
        assert (assets / "fr" / "step-01.json").exists()
        assert (assets / "nl" / "step-01.json").exists()

    def test_flat_layout_falls_back_when_locale_dir_missing(self, tmp_path):
        _make_scenario(tmp_path)
        # Only flat screenshots — no per-locale subdir.
        _write_step(tmp_path / "screenshots" / "onboarding", 1)
        _write_step(tmp_path / "screenshots" / "onboarding", 2)

        cfg = _build_config(tmp_path, target="static")
        BuildAssembler(cfg, tmp_path).build_all()

        out_dir = tmp_path / "output" / "dist"
        fr_html = (out_dir / "onboarding" / "fr.html").read_text(encoding="utf-8")
        nl_html = (out_dir / "onboarding" / "nl.html").read_text(encoding="utf-8")

        # Both locales reference the flat path (no locale segment).
        assert "../assets/onboarding/step-01.png" in fr_html
        assert "../assets/onboarding/step-01.png" in nl_html
        # No locale-segmented URLs leak in.
        assert "../assets/onboarding/fr/" not in fr_html
        assert "../assets/onboarding/nl/" not in nl_html

        # Assets are copied flat — no per-locale subdirs created.
        assets = out_dir / "assets" / "onboarding"
        assert (assets / "step-01.png").exists()
        assert not (assets / "fr").exists()
        assert not (assets / "nl").exists()


class TestPerLocaleScreenshotsAngular:
    def test_angular_url_includes_locale_segment(self, tmp_path):
        _make_scenario(tmp_path)
        for loc in ("fr", "nl"):
            _write_step(tmp_path / "screenshots" / "onboarding" / loc, 1)
            _write_step(tmp_path / "screenshots" / "onboarding" / loc, 2)

        cfg = _build_config(tmp_path, target="angular")
        BuildAssembler(cfg, tmp_path).build_all()

        out_dir = tmp_path / "output" / "dist"
        content = (out_dir / "onboarding" / "onboarding.content.ts").read_text(encoding="utf-8")
        assert "/assets/editor/onboarding/fr/step-01.png" in content
        assert "/assets/editor/onboarding/nl/step-01.png" in content
