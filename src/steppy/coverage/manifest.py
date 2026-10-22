"""Declared-surface ingest: OpenAPI endpoints + optional sitemap routes.

``refresh_declared`` is the one entry point: it loads each configured
manifest (http(s) URL via httpx, or a local file path — local paths keep
tests offline and support repos that commit their spec), parses it as
**JSON or YAML** (a served ``/openapi.json`` is the first, a spec committed
to a repo is usually the second), replaces the
per-source declared rows in the store, then runs the **re-link pass**:
observed endpoint nodes (coverage but no declared row) whose evidence
samples now match a declared template get their coverage re-pointed onto
the declared node. That makes ``merge -> refresh`` and ``refresh -> merge``
converge to the same map regardless of order.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from .normalize import compile_extra_patterns, match_template, normalize_route
from .store import (
    DeclaredEntry,
    observed_endpoint_nodes,
    replace_declared_source,
    repoint_node_coverage,
)

_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class ManifestError(RuntimeError):
    """Raised when a manifest cannot be loaded or parsed."""


def load_text(location: str) -> str:
    """Load manifest text from an http(s) URL or a local file path."""
    if location.startswith(("http://", "https://")):
        import httpx  # local import: keeps merge/report paths network-free

        try:
            response = httpx.get(location, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ManifestError(f"failed to fetch {location}: {exc}") from exc
        return response.text
    path = Path(location)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"failed to read {location}: {exc}") from exc


def parse_manifest_document(text: str, api_name: str):
    """Parse an OpenAPI document from either JSON or YAML.

    JSON is tried first: it is what an application serves at ``/openapi.json``,
    it is the fast path on a large spec, and its error names the exact offset.
    YAML is the fallback because a spec **committed to a repo** is usually
    ``.yaml`` — and ``load_text`` has always accepted a local path, so refusing
    to parse the file it fetched was a gap rather than a policy.

    Not YAML-only, even though YAML is a JSON superset: parsing a multi-megabyte
    generated spec through the YAML loader is markedly slower, for a document
    that is already known to be JSON in the common case.

    The return type is deliberately unconstrained — shape validation belongs to
    :func:`parse_openapi_endpoints`, which is where a non-mapping document has to
    be rejected anyway, and duplicating the check would let the two disagree.
    """
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"OpenAPI for {api_name!r} is not valid JSON or YAML: {exc}") from exc


def parse_openapi_endpoints(doc: dict, api_name: str) -> list[DeclaredEntry]:
    """Extract ``paths`` x methods from an OpenAPI document.

    Non-operation keys on path items (``parameters``, ``servers``, ``$ref``,
    ``x-*`` extensions, ``summary``/``description``) are skipped.
    """
    if not isinstance(doc, dict):
        # Format-neutral: the document may have arrived as YAML, and a YAML file
        # that parses to a list would otherwise be reported as bad JSON.
        raise ManifestError(f"OpenAPI document for {api_name!r} is not a mapping")
    paths = doc.get("paths") or {}
    entries: list[DeclaredEntry] = []
    for path in sorted(paths):
        item = paths[path]
        if not isinstance(item, dict):
            continue
        for method in _HTTP_METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            details = {k: operation[k] for k in ("operationId", "summary", "tags") if k in operation}
            entries.append(
                DeclaredEntry(
                    kind="endpoint",
                    key=path,
                    api=api_name,
                    method=method.upper(),
                    details=details,
                )
            )
    return entries


def parse_sitemap_routes(xml_text: str, route_param_patterns: list[str] | None = None) -> list[DeclaredEntry]:
    """Extract ``<loc>`` URLs from a sitemap, normalized to route patterns.

    Namespace-agnostic (matches any ``*}loc`` tag). Concrete URLs are run
    through the same route normalization as observed navigations so declared
    and observed route nodes share identities.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ManifestError(f"invalid sitemap XML: {exc}") from exc
    extra = compile_extra_patterns(route_param_patterns or [])
    keys = sorted(
        {
            normalize_route(el.text.strip(), extra)
            for el in root.iter()
            if el.tag.rsplit("}", 1)[-1] == "loc" and el.text and el.text.strip()
        }
    )
    return [DeclaredEntry(kind="route", key=key) for key in keys]


def relink_observed_endpoints(conn) -> int:
    """Re-point coverage from observed endpoint nodes onto declared templates.

    For each endpoint node with coverage but no declared row, retry its
    evidence samples (raw relative paths) against the declared templates of
    the same (api, method). First matching sample wins (samples are sorted,
    so this is deterministic). The emptied observed node is kept — nodes are
    never deleted implicitly.
    """
    templates: dict[tuple[str, str], dict[str, int]] = {}
    for row in conn.execute(
        "SELECT DISTINCT n.id, n.api, n.method, n.key FROM nodes n"
        " JOIN declared d ON d.node_id = n.id WHERE n.kind = 'endpoint'"
    ):
        templates.setdefault((row["api"], row["method"]), {})[row["key"]] = int(row["id"])

    moved = 0
    for candidate in observed_endpoint_nodes(conn):
        candidates = templates.get((candidate["api"], candidate["method"]))
        if not candidates:
            continue
        target_key = None
        for sample in candidate["samples"]:
            target_key = match_template(sample, list(candidates))
            if target_key:
                break
        if target_key:
            moved += repoint_node_coverage(conn, candidate["id"], candidates[target_key])
    return moved


def refresh_declared(
    conn,
    apis: list[dict],
    sitemap: str | None = None,
    route_param_patterns: list[str] | None = None,
    *,
    updated_at: str = "",
) -> dict:
    """Refresh the whole declared surface and run the re-link pass.

    ``apis`` entries are ``{"name": str, "openapi": <url-or-path>}``. Each
    manifest source is replaced independently (``openapi:<name>`` /
    ``sitemap``); triage flags and nodes are never touched.
    """
    summary: dict = {"apis": [], "sitemap": None, "relinked": 0}
    for api in apis:
        doc = parse_manifest_document(load_text(api["openapi"]), api["name"])
        entries = parse_openapi_endpoints(doc, api["name"])
        result = replace_declared_source(conn, f"openapi:{api['name']}", entries, updated_at=updated_at)
        summary["apis"].append(result)
    if sitemap:
        entries = parse_sitemap_routes(load_text(sitemap), route_param_patterns)
        summary["sitemap"] = replace_declared_source(conn, "sitemap", entries, updated_at=updated_at)
    summary["relinked"] = relink_observed_endpoints(conn)
    return summary
