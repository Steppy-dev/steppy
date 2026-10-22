"""Vendored ``@steppy/player`` build artifacts.

The cursor IIFE bundle (legacy stepbystep cursor animation), the
``<steppy-guide>`` ESM bundle (interactive player) and the video
renderer IIFE live here as pre-built artifacts, **committed to git** so
``pip install git+…`` needs no Node toolchain. After changing
``packages/player/src/``, run ``npm --prefix packages/player run build``
and copy those three bundles (with their maps) back here; CI compares them
byte for byte, and the release pipeline rebuilds them for the wheel.

The fourth bundle the player build produces, the in-app tour overlay
(``steppy-tour.js``), is deliberately not vendored: it ships to apps
through npm, and no Python code serves it.
"""

import importlib.resources
from functools import lru_cache
from pathlib import Path


class CursorBundleMissingError(FileNotFoundError):
    """Raised when the legacy cursor IIFE bundle hasn't been built yet."""


_BUILD_HINT = "Run `npm --prefix packages/player install && npm --prefix packages/player run build` to populate it."


@lru_cache(maxsize=1)
def get_legacy_cursor_iife() -> str:
    """Return the bundled cursor IIFE JS as a string.

    Single source of truth for the cursor animation. Both
    ``templates/guide.html.j2`` (standalone HTML guide) and
    ``templates/static/cursor.js.j2`` (build static target) read this
    via the ``cursor_iife`` Jinja context variable.
    """
    pkg = importlib.resources.files("steppy") / "_player_bundle" / "steppy-cursor-legacy.js"
    try:
        return pkg.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise CursorBundleMissingError(
            "Vendored cursor bundle missing at src/steppy/_player_bundle/steppy-cursor-legacy.js. " + _BUILD_HINT
        ) from exc


def get_player_bundle_path() -> Path:
    """Filesystem path of the ESM player bundle (steppy-player.js).

    Used by ``steppy build`` to copy the bundle next to the emitted
    ``guide.json`` so ``<steppy-guide>`` works on both Angular and static
    targets without a CDN.
    """
    path = bundle_dir() / "steppy-player.js"
    if not path.is_file():
        raise CursorBundleMissingError(f"Vendored player bundle missing at {path}. " + _BUILD_HINT)
    return path


def get_cursor_bundle_path() -> Path:
    """Filesystem path of the legacy cursor IIFE bundle (steppy-cursor-legacy.js).

    Used by ``steppy build`` (Angular target) to copy the bundle into the
    assets dir as ``steppy-cursor.js``. The generated per-guide component loads
    it once and calls ``window.__STEPPY_CURSOR__.mount(...)`` to animate the
    stepbystep cursor — the static target inlines the same source via
    ``get_legacy_cursor_iife()`` instead.
    """
    path = bundle_dir() / "steppy-cursor-legacy.js"
    if not path.is_file():
        raise CursorBundleMissingError(f"Vendored cursor bundle missing at {path}. " + _BUILD_HINT)
    return path


@lru_cache(maxsize=1)
def get_video_iife() -> str:
    """Return the bundled video-renderer IIFE as a string.

    Exposes ``window.__STEPPY_VIDEO__``. Unlike the other two bundles this one
    is **never copied into a build's output**: ``steppy/video.py`` inlines it
    into a throwaway harness page, drives it frame by frame over CDP, and
    deletes the page. Published guide sites get an ``.mp4``, not a renderer.
    """
    path = bundle_dir() / "steppy-video.js"
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise CursorBundleMissingError(f"Vendored video bundle missing at {path}. " + _BUILD_HINT) from exc


def get_video_bundle_path() -> Path:
    """Filesystem path of the video-renderer IIFE bundle (steppy-video.js)."""
    path = bundle_dir() / "steppy-video.js"
    if not path.is_file():
        raise CursorBundleMissingError(f"Vendored video bundle missing at {path}. " + _BUILD_HINT)
    return path


def bundle_dir() -> Path:
    """Filesystem path of the vendored bundle directory."""
    return Path(__file__).resolve().parent
