"""Capture-directory resolution must agree across all four consumers.

Four separate pieces of code answer "given slug + locale + layout, which capture
directory applies?":

  * the READ side       -- ``guide.py`` ``_resolve_scenario_dir`` (where PNGs and
    the bbox sidecars are loaded from, i.e. what the guide is built out of)
  * the COPY side       -- ``build.py`` ``_copy_screenshots`` (where those files
    are copied to inside the build's assets tree)
  * the stepbystep URL  -- ``build.py``'s ``make_url_fn`` (the ``<img src>``)
  * the interactive URL -- ``build.py``'s ``_emit_interactive_guide.screenshot_url``
    (the ``screenshot`` fields in ``guide.json``)

They all now derive from one resolver, ``GuideAssembler.resolve_capture_segments``.
When they disagreed, the build still reported success while the published guide
rendered broken images — and because the two URL builders disagreed with each
*other*, a guide could render correctly in stepbystep mode and show a blank stage
in interactive mode from the same build.

Asserting on literal URL strings cannot catch that, so these tests pin the
*invariant* instead:

    every screenshot URL the build emits -- in the HTML and in the manifest --
    must resolve to a file that actually exists in the build output.

Regressions guarded here:
  * ``layouts:`` declared while the screenshots have no per-layout subdirectory
    (captured before ``--layouts`` was added, or never re-captured): the
    stepbystep URL builder appended the layout segment unconditionally while the
    copier did not, so every screenshot 404'd;
  * flat (locale-less) captures with ``modes: ['interactive']``: the manifest URL
    closure hardcoded a ``<locale>`` segment the copier never wrote;
  * falling back past a configured layout is legal but must now WARN, since
    otherwise every layout silently renders the same screenshots.
"""

import json
import re
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.build import BuildAssembler
from steppy.build_models import BuildConfig


# ── fixtures ────────────────────────────────────────────────────────────────


def _minimal_png() -> bytes:
    import struct
    import zlib

    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    return sig + ihdr + idat + chunk(b"IEND", b"")


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


def _make_scenario(tmp_path: Path) -> None:
    root = tmp_path / "scenarios"
    root.mkdir(parents=True, exist_ok=True)
    (root / "onboarding.md").write_text(
        dedent("""\
        ---
        title: Onboarding
        tags:
          - user-guide-editor
        guide:
          enabled: true
        ---

        ### 1. Open
        - role: editor
        - action: navigate
        - target: /
        - screenshot: editor
        - guide-text:
            fr: "Ouvrez"

        ### 2. Click
        - role: editor
        - action: click
        - selector: button#go
        - screenshot: editor
        - guide-text:
            fr: "Cliquez"
        """),
        encoding="utf-8",
    )


def _config(with_layouts: bool, *, modes: list[str] | None = None) -> dict:
    cfg = dict(
        scenariosRoot="./scenarios",
        screenshotsDir="./screenshots",
        outputDir="./output/dist",
        assetsDir="./output/assets",
        role="editor",
        locales=["fr"],
        defaultLocale="fr",
        target="static",
        site={"title": "Test"},
        guides=[
            {
                "slug": "onboarding",
                "title": "Onboarding",
                "scenario": "onboarding.md",
                "modes": modes or ["stepbystep"],
            }
        ],
        navbar={"order": ["onboarding"]},
    )
    if with_layouts:
        cfg["layouts"] = ["desktop"]
        cfg["defaultLayout"] = "desktop"
    return cfg


# The capture shapes a project can legitimately have on disk. The subdir is
# relative to ``screenshots/onboarding/``; "" means the flat (legacy) layout.
CAPTURE_SHAPES = {
    "locale_and_layout": ["fr/desktop"],
    "locale_only": ["fr"],
    "layout_only": ["desktop"],
    "flat": [""],
    # Ambiguous/legacy: both dimensions present as siblings, neither nested.
    # Locale wins (it decides the UI language actually visible in the shot).
    "locale_and_layout_siblings": ["fr", "desktop"],
}


def _build(tmp_path: Path, subdirs: list[str], *, with_layouts: bool, modes: list[str] | None = None) -> Path:
    _make_scenario(tmp_path)
    for sd in subdirs:
        folder = tmp_path / "screenshots" / "onboarding"
        if sd:
            folder = folder / sd
        _write_step(folder, 1)
        _write_step(folder, 2)

    cfg = BuildConfig(**_config(with_layouts, modes=modes))
    BuildAssembler(cfg, tmp_path).build_all()
    return tmp_path / "output" / "dist"


def _img_srcs(html: str) -> list[str]:
    return re.findall(r'<img[^>]+src="([^"]+)"', html)


# ── the invariant ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", sorted(CAPTURE_SHAPES))
@pytest.mark.parametrize("with_layouts", [False, True], ids=["no-layouts", "layouts"])
def test_build_never_silently_emits_a_broken_screenshot_url(tmp_path, shape, with_layouts):
    """The load-bearing invariant: a build either fails loudly or is correct.

    Two acceptable outcomes for any capture shape:
      * the build raises with a clear "Missing screenshot" — the correct
        response to a genuine misconfiguration (screenshots captured per-layout
        while the build declares no layouts, so there is nothing it can read);
      * the build succeeds and *every* ``<img src>`` resolves to a real file.

    The third outcome — succeeds, reports success, and emits URLs that 404 — is
    what this test exists to forbid. Before the fix that is exactly what
    happened for `locale_only`, `flat` and `locale_and_layout_siblings` whenever
    ``layouts`` was configured: the URL builder appended the layout segment
    unconditionally while the copier never created that directory.
    """
    try:
        out_dir = _build(tmp_path, CAPTURE_SHAPES[shape], with_layouts=with_layouts)
    except ValueError as exc:
        # Loud failure is a valid outcome — but it must actually name the problem.
        assert "screenshot" in str(exc).lower(), f"build failed with an unhelpful error: {exc}"
        return

    page = out_dir / "onboarding" / "fr.html"
    assert page.is_file(), "build reported success but generated no guide page"

    srcs = _img_srcs(page.read_text(encoding="utf-8"))
    assert srcs, "guide page contains no screenshots — the assertion below would be vacuous"

    missing = []
    for src in srcs:
        # Static target emits URLs relative to <slug>/<locale>.html.
        resolved = (page.parent / src).resolve()
        if not resolved.is_file():
            missing.append(f"{src}  ->  {resolved}")

    assert not missing, (
        f"{len(missing)}/{len(srcs)} screenshot URL(s) point at files the build never wrote "
        f"(shape={shape}, layouts={'on' if with_layouts else 'off'}):\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize("shape", sorted(CAPTURE_SHAPES))
@pytest.mark.parametrize("with_layouts", [False, True], ids=["no-layouts", "layouts"])
def test_interactive_manifest_urls_resolve_too(tmp_path, shape, with_layouts):
    """The interactive manifest is a *fourth* consumer of the same resolution.

    It has its own URL closure, which used to hardcode a ``<locale>`` segment.
    A guide could therefore render correctly in stepbystep mode and show a blank
    stage in interactive mode, from the same build — the hardest possible
    failure to attribute. Same invariant, different emitter.
    """
    try:
        out_dir = _build(
            tmp_path,
            CAPTURE_SHAPES[shape],
            with_layouts=with_layouts,
            modes=["stepbystep", "interactive"],
        )
    except ValueError as exc:
        assert "screenshot" in str(exc).lower(), f"build failed with an unhelpful error: {exc}"
        return

    manifest_path = out_dir / "onboarding" / "guide.json"
    assert manifest_path.is_file(), "interactive mode was configured but no guide.json was emitted"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    urls: list[str] = []

    def _collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "screenshot" and isinstance(value, str):
                    urls.append(value)
                else:
                    _collect(value)
        elif isinstance(node, list):
            for item in node:
                _collect(item)

    _collect(manifest)
    assert urls, "manifest contains no screenshot URLs — the assertion below would be vacuous"

    missing = [
        f"{u}  ->  {(manifest_path.parent / u).resolve()}"
        for u in urls
        if not (manifest_path.parent / u).resolve().is_file()
    ]
    assert not missing, (
        f"{len(missing)}/{len(urls)} manifest screenshot URL(s) 404 "
        f"(shape={shape}, layouts={'on' if with_layouts else 'off'}) — the interactive "
        f"player would render a blank stage:\n  " + "\n  ".join(missing)
    )


def test_missing_layout_capture_warns_instead_of_degrading_silently(tmp_path, capsys):
    """Falling back past a configured layout is legal but must not be silent.

    Making the URL builder agree with the copier removed the 404, but on its own
    it would have traded a visibly broken build for a silently wrong one: every
    layout resolves to the same screenshots, so the page ships a layout toggle
    that swaps between identical images, and a step restricted to one layout is
    rendered with another layout's capture. The build must say so.
    """
    _build(tmp_path, CAPTURE_SHAPES["locale_only"], with_layouts=True)
    err = capsys.readouterr().err

    assert "WARNING" in err and "desktop" in err, (
        f"a configured layout with no capture on disk degraded silently; stderr was:\n{err}"
    )
    # Actionable, not just noise.
    assert "steppy run --guide --layouts desktop" in err


def test_no_warning_when_the_layout_capture_is_present(tmp_path, capsys):
    """The warning must not cry wolf on a correctly-captured project."""
    _build(tmp_path, CAPTURE_SHAPES["locale_and_layout"], with_layouts=True)
    assert "WARNING" not in capsys.readouterr().err


@pytest.mark.parametrize("shape", sorted(CAPTURE_SHAPES))
def test_read_and_copy_sides_agree_on_the_same_directory(tmp_path, shape):
    """The guide HTML must be built from the screenshots that get deployed.

    A build can emit URLs that resolve while still being wrong: if the READ side
    reads step-01.png out of ``<slug>/desktop/`` but the COPY side deploys
    ``<slug>/fr/``, the page renders the wrong image. Pin that they resolve to
    the same directory.
    """
    from steppy.guide import GuideAssembler
    from steppy.models import SteppyConfig, AppConfig, AppAuthConfig

    _make_scenario(tmp_path)
    for sd in CAPTURE_SHAPES[shape]:
        folder = tmp_path / "screenshots" / "onboarding"
        if sd:
            folder = folder / sd
        _write_step(folder, 1)

    shots = tmp_path / "screenshots"
    steppy_cfg = SteppyConfig(apps={"editor": AppConfig(name="N", url="http://x", auth=AppAuthConfig(mode="bypass"))})
    read_dir = GuideAssembler(steppy_cfg)._resolve_scenario_dir("onboarding", shots, "fr", "desktop")

    cfg = BuildConfig(**_config(with_layouts=True))
    asm = BuildAssembler(cfg, tmp_path)
    results: dict[str, str] = {}

    class _Scenario:
        slug = "onboarding"

    asm._copy_screenshots(cfg.guides[0], _Scenario(), shots, tmp_path / "assets", results, True)
    copy_srcs = {Path(k).parent.name for k in results}

    assert read_dir.name in copy_srcs or read_dir.name == "onboarding", (
        f"read side loads from {read_dir} but the copier deploys {sorted(copy_srcs)} "
        f"(shape={shape}) — the guide would show screenshots that were never deployed"
    )
