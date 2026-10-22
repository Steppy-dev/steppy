"""URL / route / request normalization — pure, deterministic, web-standard.

Operates only on URL structure (scheme/host/path/query/fragment): no
framework assumptions. Three jobs:

- ``normalize_route`` — observed URL -> route pattern. Dynamic segments
  (integers, UUIDs, long hex, ISO dates, plus project-configured regexes)
  collapse to ``:id`` so ``/invoices/123`` and ``/invoices/456`` cluster.
  Hash-based SPA routing (``#/path``) is treated as the route path.
  Word segments are deliberately left alone — clustering ``/settings/profile``
  with ``/settings/billing`` would be wrong, so only provably-dynamic shapes
  normalize.
- ``assign_api`` / ``classify_request`` — attribute an observed network
  request to a configured API by longest base-URL prefix match (base URLs
  must mirror the OpenAPI ``servers[].url``, including any path prefix, and
  should include Docker-internal ``internal_url`` origins for parallel runs).
- ``match_template`` — concrete path -> declared OpenAPI template
  (segment-wise; literal segments exact, ``{param}``/``:param`` match any
  non-empty segment; most-literal-segments wins, so ``/users/me`` beats
  ``/users/{id}``).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_DYNAMIC_SEGMENT_RES = (
    re.compile(r"^\d+$"),  # integer ids
    re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),  # UUID
    re.compile(r"^[0-9a-fA-F]{16,}$"),  # long hex tokens (hashes, mongo ids)
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),  # ISO dates
)

PARAM_TOKEN = ":id"


def _is_dynamic_segment(segment: str, extra_patterns: tuple[re.Pattern, ...] = ()) -> bool:
    for pattern in _DYNAMIC_SEGMENT_RES + tuple(extra_patterns):
        if pattern.match(segment):
            return True
    return False


def compile_extra_patterns(patterns: list[str]) -> tuple[re.Pattern, ...]:
    """Compile ``coverage.route_param_patterns`` once for reuse across a merge."""
    return tuple(re.compile(p) for p in patterns)


def split_segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def normalize_path(path: str, extra_patterns: tuple[re.Pattern, ...] = ()) -> str:
    """Collapse dynamic segments of a path to ``:id``; strip trailing slash."""
    segments = split_segments(path)
    if not segments:
        return "/"
    normalized = [PARAM_TOKEN if _is_dynamic_segment(s, extra_patterns) else s for s in segments]
    return "/" + "/".join(normalized)


def route_path_from_url(url: str) -> str:
    """Extract the route path from a URL, hash-routing aware.

    A fragment starting with ``/`` is hash-based SPA routing — the fragment
    (minus its own query part) IS the route. Query strings and ordinary
    fragments are dropped.
    """
    parts = urlsplit(url)
    if parts.fragment.startswith("/"):
        return parts.fragment.split("?", 1)[0] or "/"
    return parts.path or "/"


def normalize_route(url: str, extra_patterns: tuple[re.Pattern, ...] = ()) -> str:
    """Observed URL (full or path-only) -> normalized route pattern."""
    return normalize_path(route_path_from_url(url), extra_patterns)


def strip_query(url_or_path: str) -> str:
    """Drop query string and fragment — used when storing evidence samples."""
    return url_or_path.split("#", 1)[0].split("?", 1)[0]


def _base_parts(base_url: str) -> tuple[str, str, str]:
    parts = urlsplit(base_url)
    return (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"))


def assign_api(url: str, apis: list[dict]) -> tuple[str, str] | None:
    """Match ``url`` to a configured API by longest base-URL prefix.

    ``apis`` entries are ``{"name": str, "base_urls": [str, ...]}``.
    Returns ``(api_name, path_relative_to_base)`` or ``None``. Prefix
    matching is segment-aligned: base ``http://h/api`` matches
    ``http://h/api/users`` but not ``http://h/apiv2``.
    """
    parts = urlsplit(url)
    scheme, netloc, path = parts.scheme.lower(), parts.netloc.lower(), parts.path
    best: tuple[int, str, str] | None = None
    for api in apis:
        for base in api.get("base_urls", []):
            b_scheme, b_netloc, b_path = _base_parts(base)
            if (b_scheme, b_netloc) != (scheme, netloc):
                continue
            if b_path and not (path == b_path or path.startswith(b_path + "/")):
                continue
            if best is None or len(b_path) > best[0]:
                rel = path[len(b_path) :] if b_path else path
                best = (len(b_path), api["name"], rel or "/")
    if best is None:
        return None
    _, name, rel = best
    if not rel.startswith("/"):
        rel = "/" + rel
    return name, rel


def classify_request(
    method: str,
    url: str,
    resource_type: str,
    apis: list[dict],
    api_resource_types: tuple[str, ...] = ("xhr", "fetch"),
) -> dict | None:
    """Classify an observed network request as an API call, or ``None``.

    API calls are requests of the configured resource types (web-standard
    Playwright resource types — default xhr + fetch) over http(s). Requests
    matching no configured API are kept with ``api=''`` so the report can
    surface them as unclassified instead of silently dropping signal.
    """
    if resource_type not in api_resource_types:
        return None
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        return None
    assigned = assign_api(url, apis)
    if assigned is None:
        return {"api": "", "method": method.upper(), "path": parts.path or "/"}
    name, rel = assigned
    return {"api": name, "method": method.upper(), "path": rel}


def _is_param_segment(segment: str) -> bool:
    return (segment.startswith("{") and segment.endswith("}")) or segment.startswith(":")


def match_template(raw_path: str, templates: list[str]) -> str | None:
    """Match a concrete path against declared path templates.

    Segment-wise: equal segment count; literal segments compare exactly;
    ``{param}`` / ``:param`` segments match any non-empty segment. Ties break
    toward the template with the most literal segments (``/users/me`` beats
    ``/users/{id}``), then lexicographically for full determinism.
    """
    raw_segments = split_segments(strip_query(raw_path))
    best: tuple[int, str] | None = None
    for template in sorted(templates):
        t_segments = split_segments(template)
        if len(t_segments) != len(raw_segments):
            continue
        literals = 0
        for raw, tpl in zip(raw_segments, t_segments):
            if _is_param_segment(tpl):
                continue
            if raw != tpl:
                break
            literals += 1
        else:
            if best is None or literals > best[0]:
                best = (literals, template)
    return best[1] if best else None
