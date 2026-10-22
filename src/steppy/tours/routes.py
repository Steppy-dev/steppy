"""Entry routes: from a scenario's navigate target to a tour's route pattern.

A tour may declare where it starts (``tour.entry: /invoices/:invoiceId``) or
inherit it from the scenario's navigation. Either way the stored route is
**base-relative** — the path *inside* the app, with the app URL's own path
prefix removed — because the overlay matches it against the document's base
URL (``<base href>``) and the app's router navigates to it.

Navigate targets are resolved the way the generated tests resolve ``goto()``:
relative to ``apps.<role>.url``. A ``{{var}}`` segment becomes a ``:var``
parameter; literal segments are percent-encoded the way ``location.pathname``
spells them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

from ..tour_spec import is_dot_segment, is_route
from .settings import origin_of

#: Segments that look like fixture ids: the tour cannot know them in production.
ID_LIKE_RE = re.compile(r"\d+|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}|(?=.*\d)[0-9a-fA-F]{8,}")
_VAR_SEG = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]{0,63})\s*\}\}")
_PARAM_SEG = re.compile(r":([A-Za-z_][A-Za-z0-9_]{0,63})")
_SAFE = "._~!$&()*+,;=@-%"


@dataclass(frozen=True)
class AppBase:
    origin: Optional[str]
    path: str  # always ends with "/"
    unresolved: bool = False


def app_base(app_url: Optional[str]) -> AppBase:
    """Origin and path prefix of an app URL; ``unresolved`` when it has no usable host."""
    if not app_url or "${" in app_url:
        return AppBase(None, "/", unresolved=True)
    origin = origin_of(app_url)
    if origin is None:
        return AppBase(None, "/", unresolved=True)
    path = urlsplit(app_url).path or "/"
    return AppBase(origin, path if path.endswith("/") else path + "/")


@dataclass
class RouteResult:
    """A route pattern, or the reason there is none, plus non-fatal notes."""

    segments: Optional[list[str]] = None  # pattern segments (":param" or encoded literal)
    raw_segments: list[str] = field(default_factory=list)  # the target's own segments (for binding)
    error: Optional[str] = None
    query_dropped: bool = False
    id_like: list[str] = field(default_factory=list)

    @property
    def route(self) -> Optional[str]:
        if self.segments is None:
            return None
        return "/" + "/".join(self.segments)


def _target_path(raw: str, base: AppBase) -> tuple[Optional[str], Optional[str], bool]:
    """``(base-relative path, error, query_dropped)`` for a navigate target."""
    text = raw.strip()
    if "#" in text:
        return None, "hash routes are not supported by in-app tours (v1)", False
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text) or text.startswith("//"):
        if base.origin is None:
            return None, "an absolute URL cannot be checked against an unresolved app URL — set tour.entry", False
        if origin_of(text) != base.origin:
            return None, f"{text!r} is not on the app's origin ({base.origin}) — a tour cannot navigate there", False
        parts = urlsplit(text)
        path, query = parts.path or "/", parts.query
    else:
        path, _, query = text.partition("?")
    if not path.startswith("/"):
        path = base.path + path
    if base.path != "/":
        prefix = base.path.rstrip("/")
        if path == prefix:
            path = "/"
        elif path.startswith(base.path):
            path = "/" + path[len(base.path) :]
        else:
            return None, f"{text!r} is outside the app's base path {base.path}", bool(query)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return path, None, bool(query)


def _segments(path: str) -> list[str]:
    return [] if path == "/" else path.split("/")[1:]


def _literal(seg: str) -> Optional[str]:
    if not seg or is_dot_segment(seg):
        return None
    return quote(seg, safe=_SAFE)


def route_from_target(raw: str, base: AppBase) -> RouteResult:
    """Inferred entry: the navigate target as a pattern (``{{var}}`` → ``:var``)."""
    path, error, query = _target_path(raw, base)
    if error:
        return RouteResult(error=error, query_dropped=query)
    result = RouteResult(query_dropped=query, segments=[])
    for seg in _segments(path):
        result.raw_segments.append(seg)
        var = _VAR_SEG.fullmatch(seg)
        if var:
            result.segments.append(":" + var.group(1))
            continue
        if "{{" in seg:
            return RouteResult(error=f"segment {seg!r} is only partly a {{{{var}}}}; make it a whole segment")
        lit = _literal(seg)
        if lit is None:
            return RouteResult(error=f"segment {seg!r} is not a valid path segment")
        if ID_LIKE_RE.fullmatch(unquote(seg)):
            result.id_like.append(seg)
        result.segments.append(lit)
    if not is_route(result.route):
        return RouteResult(error=f"{result.route!r} is not a route the in-app player accepts")
    return result


def parse_entry_pattern(text: str) -> RouteResult:
    """Explicit ``tour.entry``: already base-relative; ``:name`` and ``{{name}}`` are params."""
    path = text.strip()
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    result = RouteResult(segments=[])
    for seg in _segments(path):
        param = _PARAM_SEG.fullmatch(seg) or _VAR_SEG.fullmatch(seg)
        if param:
            result.segments.append(":" + param.group(1))
            continue
        lit = _literal(seg)
        if lit is None or "{{" in seg:
            return RouteResult(error=f"tour.entry segment {seg!r} is not a valid path segment")
        result.segments.append(lit)
    if not is_route(result.route):
        return RouteResult(error=f"tour.entry {text!r} is not a route the in-app player accepts")
    return result


def target_segments(raw: str, base: AppBase) -> Optional[list[str]]:
    """The raw base-relative segments of a navigate target, or None when unusable."""
    path, error, _ = _target_path(raw, base)
    return None if error else _segments(path)


def matches(pattern: list[str], target: list[str]) -> bool:
    """Segment-wise match: ``:p`` takes any segment; a literal must equal a literal."""
    if len(pattern) != len(target):
        return False
    for pat, seg in zip(pattern, target):
        if pat.startswith(":"):
            continue
        if _VAR_SEG.fullmatch(seg) or pat != _literal(seg):
            return False
    return True


def bind(pattern: list[str], target: list[str]) -> tuple[tuple[str, str], ...]:
    """``(param, template)`` pairs: the target's text for each ``:param`` (``{{var}}`` kept)."""
    out = []
    for pat, seg in zip(pattern, target):
        if pat.startswith(":"):
            out.append((pat[1:], seg if _VAR_SEG.fullmatch(seg) else unquote(seg)))
    return tuple(out)
