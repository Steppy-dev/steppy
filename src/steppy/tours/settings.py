"""A resolved, immutable view of ``config.yaml``'s ``tours:`` block."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from ..captures import DEFAULT_TOURS_DIR, resolve_output_dir
from ..locales import base_language
from ..models import DEFAULT_LOCALE, DEFAULT_LOCALES, SteppyConfig
from ..tour_spec import CHROME_STRING_LOCALES
from .diagnostics import Diagnostic


def origin_of(url: str) -> Optional[str]:
    """``scheme://host[:port]`` with default ports dropped, or None if not http(s)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    default = {"http": 80, "https": 443}[parts.scheme]
    port = parts.port
    return f"{parts.scheme}://{parts.hostname.lower()}" + (f":{port}" if port and port != default else "")


@dataclass(frozen=True)
class ToursSettings:
    enabled: bool
    default_opt_in: bool
    build_id_raw: str
    locales: tuple[str, ...]
    default_locale: str
    test_id_attribute: str
    guide_base_url: Optional[str]
    guide_origin: Optional[str]
    layouts: tuple[tuple[str, str], ...]
    replay: bool
    project_root: Path
    tours_root: Path
    app_assets_dirs: tuple[Path, ...]
    publishes_guide: bool
    app_urls: dict = field(default_factory=dict)  # role -> configured app URL
    screenshot_widths: dict = field(default_factory=dict)  # layout -> viewport width
    info_target_click: Optional[str] = None  # default `tour-target-click` of a targeted info step

    @property
    def layout_map(self) -> dict[str, str]:
        return dict(self.layouts)

    @classmethod
    def from_config(cls, config: SteppyConfig, project_root: Path) -> "ToursSettings":
        tours = config.tours
        if tours is None:
            raise ValueError("config.yaml has no tours: block")
        root = Path(project_root).resolve()
        locales = tuple(tours.locales or config.locales or DEFAULT_LOCALES)
        default_locale = tours.default_locale or config.default_locale or DEFAULT_LOCALE
        base = tours.guide_base_url
        if base is not None and "${" in base:
            base = None  # unresolved: no guide links (reported by settings_diagnostics)
        app_dirs = tuple(resolve_output_dir(root, t.path, t.path) for t in tours.targets if t.type == "app-assets")
        return cls(
            enabled=tours.enabled,
            default_opt_in=tours.default,
            build_id_raw=tours.build_id,
            locales=locales,
            default_locale=default_locale,
            test_id_attribute=tours.test_id_attribute,
            guide_base_url=base,
            guide_origin=origin_of(base) if base else None,
            layouts=tuple((tours.layouts or {}).items()),
            replay=tours.replay,
            project_root=root,
            tours_root=resolve_output_dir(root, config.output.tours_dir, DEFAULT_TOURS_DIR),
            app_assets_dirs=app_dirs,
            publishes_guide=any(t.type == "guide" for t in tours.targets),
            app_urls={role: app.url for role, app in config.apps.items()},
            screenshot_widths={name: layout.width for name, layout in config.screenshot.layouts.items()},
            info_target_click=tours.info_target_click,
        )


# ── Layout media-query sanity check ───────────────────────────────────────────

_TERM = re.compile(r"\(\s*(min|max)-width\s*:\s*(\d+(?:\.\d+)?)px\s*\)")
_PREFIX = re.compile(r"^(?:only\s+screen|screen|all)\s+and\s+", re.I)


def parse_simple_media(query: str) -> Optional[tuple[float, float]]:
    """``(lo, hi)`` widths a simple ``(min|max)-width: Npx`` query accepts, else None."""
    text = _PREFIX.sub("", query.strip())
    lo, hi = 0.0, float("inf")
    for part in re.split(r"\s+and\s+", text):
        m = _TERM.fullmatch(part.strip())
        if not m:
            return None
        value = float(m.group(2))
        if m.group(1) == "min":
            lo = max(lo, value)
        else:
            hi = min(hi, value)
    return lo, hi


def settings_diagnostics(config: SteppyConfig, settings: ToursSettings) -> list[Diagnostic]:
    """Config-level checks that need more than one block at a time."""
    out: list[Diagnostic] = []
    tours = config.tours
    if tours is not None and tours.guide_base_url and "${" in tours.guide_base_url:
        out.append(
            Diagnostic(
                "warning",
                "app-url-unresolved",
                f"tours.guide_base_url {tours.guide_base_url!r} references an unset environment variable; "
                "tour files get no 'Open the full guide' link",
            )
        )
    for locale in settings.locales:
        if base_language(locale) not in CHROME_STRING_LOCALES:
            out.append(
                Diagnostic(
                    "warning",
                    "chrome-strings-missing",
                    f"tour locale {locale!r} has no built-in overlay strings (buttons, progress, generic hints); "
                    f"the overlay shows them in English next to {locale} text unless your app sets the host's "
                    f"`strings` for {locale!r}",
                    hint=f"built in: {', '.join(CHROME_STRING_LOCALES)}",
                )
            )
    widths = settings.screenshot_widths
    for name, query in settings.layouts:
        bounds = parse_simple_media(query)
        width = widths.get(name)
        if bounds is None or width is None:
            out.append(
                Diagnostic(
                    "info",
                    "layout-media-unchecked",
                    f"tours.layouts[{name!r}] = {query!r} is not a simple width query; "
                    "the tour replay will still check it in the browser",
                )
            )
            continue
        lo, hi = bounds
        if not lo <= width <= hi:
            out.append(
                Diagnostic(
                    "error",
                    "layout-media-mismatch",
                    f"tours.layouts[{name!r}] = {query!r} does not match the {name} viewport width ({width}px) "
                    "from screenshot.layouts, so the overlay would skip that layout's steps where CI runs them",
                )
            )
        for other, other_width in widths.items():
            if other != name and other in dict(settings.layouts) and lo <= other_width <= hi:
                out.append(
                    Diagnostic(
                        "warning",
                        "layout-media-overlap",
                        f"tours.layouts[{name!r}] = {query!r} also matches the {other} viewport ({other_width}px); "
                        f"{name}-only steps would show on {other} screens too",
                    )
                )
    return out
