"""Capture-directory resolution must survive real-world disk shapes.

Companion to ``test_screenshot_path_resolution.py``, which pins that the four
consumers *agree*. This file pins that the resolution itself is right for shapes
a real project actually produces:

  * a crashed capture leaves an empty ``<slug>/<locale>/`` behind
    (``_screenshot_folder`` mkdirs before ``page.screenshot``);
  * captures made with ``--layouts`` but no ``--locales`` land in
    ``<slug>/<layout>/`` with no locale segment;
  * ``steppy guide`` writes its pages into ``<role>/<locale>[/<layout>]/`` while
    ``--screenshots-dir`` and ``--output-dir`` are independent options, so a
    fixed ``../../../`` prefix cannot be right in general.
"""

import json
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.guide import GuideAssembler
from steppy.models import AppAuthConfig, AppConfig, SteppyConfig


# ── fixtures ────────────────────────────────────────────────────────────────


def _png() -> bytes:
    import struct
    import zlib

    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    return (
        sig
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


def _write_step(folder: Path, idx: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"step-{idx:02d}.png").write_bytes(_png())
    (folder / f"step-{idx:02d}.json").write_text(
        json.dumps(
            {
                "viewport": {"width": 1280, "height": 720},
                "target": {"kind": "single", "bbox": {"x": 0, "y": 0, "width": 10, "height": 10}},
            }
        ),
        encoding="utf-8",
    )


def _config() -> SteppyConfig:
    return SteppyConfig(
        apps={"editor": AppConfig(name="N", url="http://x", auth=AppAuthConfig(mode="bypass"))},
        locales=["fr", "nl"],
        default_locale="fr",
    )


def _assembler() -> GuideAssembler:
    return GuideAssembler(_config())


def _scenario_file(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "demo.md"
    path.write_text(
        dedent("""\
        ---
        title: Demo
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
            nl: "Open"

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
    return path


# ── 1.1 an empty capture dir must not shadow a working one ──────────────────


def test_empty_locale_dir_does_not_shadow_a_working_flat_capture(tmp_path):
    """A crashed capture leaves an empty dir; it must not poison every later build.

    ``_screenshot_folder`` in the generated conftest does
    ``folder.mkdir(parents=True, exist_ok=True)`` *before* ``page.screenshot``,
    so a browser crash on the first step of the ``nl`` parametrization leaves
    ``<slug>/nl/`` empty. Resolving to it instead of the complete flat capture
    makes every subsequent guide/build fail until a human deletes a directory
    they never knowingly created.
    """
    shots = tmp_path / "screenshots"
    _write_step(shots / "demo", 1)  # complete flat capture
    (shots / "demo" / "nl").mkdir(parents=True)  # empty, from the crash

    loc, lay = _assembler().resolve_capture_segments("demo", shots, "nl", None)
    assert (loc, lay) == (None, None), (
        "an empty <slug>/nl/ shadowed the working flat capture; "
        "a ladder rung must require step-*.png, not merely that the dir exists"
    )


def test_locale_dir_holding_only_layout_subdirs_is_not_a_capture_dir(tmp_path):
    """`<slug>/fr/` containing only `desktop/` holds no captures of its own.

    This is the 'captured with layouts, built without layouts' shape. Matching
    the locale rung here resolves to a directory with no PNGs and raises
    'Missing screenshot' for captures that are sitting on disk one level down.
    """
    shots = tmp_path / "screenshots"
    _write_step(shots / "demo" / "fr" / "desktop", 1)

    loc, lay = _assembler().resolve_capture_segments("demo", shots, "fr", None)
    assert (loc, lay) != ("fr", None), "<slug>/fr/ matched despite holding only a layout subdir and no step-*.png"


def test_populated_dirs_still_resolve(tmp_path):
    """The predicate must not be so strict that real captures stop matching."""
    shots = tmp_path / "screenshots"
    _write_step(shots / "demo" / "fr" / "desktop", 1)
    _write_step(shots / "demo" / "nl", 1)
    asm = _assembler()

    assert asm.resolve_capture_segments("demo", shots, "fr", "desktop") == ("fr", "desktop")
    assert asm.resolve_capture_segments("demo", shots, "nl", None) == ("nl", None)


# ── 1.2 a negative resolution must not be pinned for the object's lifetime ──


def test_resolution_is_not_pinned_before_captures_exist(tmp_path):
    """An assembler reused across a capture must see the new screenshots.

    The memo's justification is 'an assembler is built after captures exist',
    but nothing enforces that — BuildAssembler constructs its GuideAssembler
    eagerly and the class is public with no invalidation hook.
    """
    shots = tmp_path / "screenshots"
    (shots / "demo").mkdir(parents=True)
    asm = _assembler()

    assert asm.resolve_capture_segments("demo", shots, "fr", None) == (None, None)

    _write_step(shots / "demo" / "fr", 1)  # captures land afterwards

    assert asm.resolve_capture_segments("demo", shots, "fr", None) == (
        "fr",
        None,
    ), "the pre-capture (None, None) answer was memoized and never re-probed"


def test_clear_capture_cache_forces_a_reprobe(tmp_path):
    """Explicit invalidation exists for consumers that need it."""
    shots = tmp_path / "screenshots"
    _write_step(shots / "demo" / "fr", 1)
    asm = _assembler()
    assert asm.resolve_capture_segments("demo", shots, "fr", None) == ("fr", None)

    import shutil

    shutil.rmtree(shots / "demo" / "fr")
    asm.clear_capture_cache()
    assert asm.resolve_capture_segments("demo", shots, "fr", None) == (None, None)


# ── 1.3 layout-only captures must be discoverable ───────────────────────────


def test_discover_layouts_finds_layout_only_captures(tmp_path):
    """`steppy run --screenshots --layouts desktop,mobile` writes no locale segment."""
    shots = tmp_path / "screenshots"
    _write_step(shots / "demo" / "desktop", 1)
    _write_step(shots / "demo" / "mobile", 1)

    assert _assembler()._discover_layouts("demo", shots, locale=None) == ["desktop", "mobile"]


def test_locale_dirs_are_not_mistaken_for_layouts(tmp_path):
    """At the flat base, `<slug>/fr/` and `<slug>/desktop/` look identical.

    Only the name distinguishes them, so the locale-less probe must exclude
    configured locales. Without that, a plain per-locale project starts
    rendering a bogus guide "per layout fr", nested at <role>/fr/fr/.
    """
    shots = tmp_path / "screenshots"
    _write_step(shots / "demo" / "fr", 1)  # a LOCALE dir (config declares fr)
    _write_step(shots / "demo" / "desktop", 1)  # a real layout dir

    assert _assembler()._discover_layouts("demo", shots, locale=None) == ["desktop"]


def test_generate_guides_for_tag_renders_layout_only_captures(tmp_path):
    """End-to-end: the layout dimension must not be silently dropped.

    Today the per-locale probe returns [] and the caller falls back to
    ``layout=None``, so resolution lands on the flat base (which holds only
    subdirectories) and every scenario reports 'Missing screenshot' for PNGs
    that are on disk.
    """
    shots = tmp_path / "screenshots"
    for layout in ("desktop", "mobile"):
        _write_step(shots / "demo" / layout, 1)
        _write_step(shots / "demo" / layout, 2)
    scenarios = tmp_path / "scenarios"
    _scenario_file(scenarios)
    out = tmp_path / "guides"

    results = _assembler().generate_guides_for_tag("user-guide-editor", ["fr"], scenarios, out, shots)

    statuses = {(slug, loc): res.get("status") for slug, per_loc in results.items() for loc, res in per_loc.items()}
    assert statuses, "no guides were generated at all"
    errors = {k: v for k, v in statuses.items() if v != "success"}
    assert not errors, f"layout-only captures were not discovered: {errors}"


# ── 1.4 standalone `steppy guide` URLs must resolve ─────────────────────────


CAPTURE_SHAPES = {
    "flat": [""],
    "locale_only": ["fr"],
    "locale_and_layout": ["fr/desktop"],
    "layout_only": ["desktop"],
}


@pytest.mark.parametrize("shape", sorted(CAPTURE_SHAPES))
def test_standalone_guide_image_urls_resolve_on_disk(tmp_path, shape):
    """Every image the standalone guide emits must point at a real file.

    ``steppy guide`` writes pages into ``<out>/<role>/<locale>[/<layout>]/`` while
    ``--output-dir`` and ``--screenshots-dir`` are independent options, so a
    hardcoded ``../../../screenshots/...`` is structurally wrong — it happens to
    resolve only for flat captures with no layout.
    """
    shots = tmp_path / "screenshots"
    for sd in CAPTURE_SHAPES[shape]:
        folder = shots / "demo" / sd if sd else shots / "demo"
        _write_step(folder, 1)
        _write_step(folder, 2)
    scenarios = tmp_path / "scenarios"
    _scenario_file(scenarios)
    out = tmp_path / "guides"

    results = _assembler().generate_guides_for_tag("user-guide-editor", ["fr"], scenarios, out, shots)
    failures = {
        (slug, loc): res
        for slug, per_loc in results.items()
        for loc, res in per_loc.items()
        if res.get("status") != "success"
    }
    assert not failures, f"guide generation failed for shape={shape}: {failures}"

    import re

    pages = list(out.rglob("*.html"))
    assert pages, "no HTML guide pages were written"
    missing = []
    for page in pages:
        for src in re.findall(r'<img[^>]+src="([^"]+)"', page.read_text(encoding="utf-8")):
            if src.startswith(("http:", "https:", "data:")):
                continue
            resolved = (page.parent / src).resolve()
            if not resolved.is_file():
                missing.append(f"{page.name}: {src} -> {resolved}")

    assert not missing, (
        f"{len(missing)} image URL(s) in the standalone guide do not resolve (shape={shape}):\n  "
        + "\n  ".join(missing)
    )
