"""Source-map decoding: executed V8 offsets -> source modules. Pure Python.

The recorder (generated conftest) persists, per script: the executed function
start offsets (V8 character offsets), a ``line_starts`` newline-offset table
computed from the script source, and a reference to the source map (inline
``data:`` maps are decoded + stored at record time; external maps are looked
up on local disk at merge time via ``coverage.sourcemap_dir``).

This module turns those into source-module sets:

  offset --(bisect over line_starts)--> (generated line, col)
         --(floor lookup in the decoded mappings line index)--> sources[idx]
         --(webpack:// / sourceRoot / dot-segment normalization, ignore
            filter)--> module key

Degradation ladder (per script, never an error): usable map -> module keys;
unusable/missing map or missing line_starts -> a single ``bundle:<url-path>``
node so the signal survives at bundle granularity.
"""

from __future__ import annotations

import gzip
import json
import posixpath
import re
from bisect import bisect_right
from pathlib import Path
from urllib.parse import urlsplit

_B64 = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}
_SCHEME_RE = re.compile(r"^[a-zA-Z][\w+.\-]*://")


class SourceMapUnusable(Exception):
    """Structural problem with a map — caller degrades to bundle granularity."""


def _decode_vlq_segment(segment: str) -> list[int]:
    values: list[int] = []
    result = shift = 0
    for ch in segment:
        try:
            digit = _B64[ch]
        except KeyError:
            raise SourceMapUnusable(f"invalid VLQ character {ch!r}") from None
        result |= (digit & 31) << shift
        if digit & 32:
            shift += 5
        else:
            values.append(-(result >> 1) if result & 1 else result >> 1)
            result = shift = 0
    if shift:
        raise SourceMapUnusable("truncated VLQ segment")
    return values


def build_line_index(mappings: str) -> list[tuple[list[int], list[int]]]:
    """Decode ``mappings`` into per-generated-line ``(gen_cols, src_idxs)``.

    ``gen_col`` resets per line; ``src_idx`` accumulates across lines (spec
    behaviour). Segments without a source field get ``src_idx = -1``.
    """
    lines: list[tuple[list[int], list[int]]] = []
    src_idx = 0
    for line_str in mappings.split(";"):
        cols: list[int] = []
        idxs: list[int] = []
        gen_col = 0
        for seg in line_str.split(","):
            if not seg:
                continue
            fields = _decode_vlq_segment(seg)
            gen_col += fields[0]
            if len(fields) >= 4:
                src_idx += fields[1]
                cols.append(gen_col)
                idxs.append(src_idx)
            else:
                cols.append(gen_col)
                idxs.append(-1)
        lines.append((cols, idxs))
    return lines


def normalize_source_path(source: str, source_root: str = "") -> str:
    """Normalize a source-map ``sources`` entry to a stable module key.

    Resolves against ``sourceRoot``, strips scheme prefixes (``webpack://``,
    ``ng://`` …) and leading slashes, and collapses dot segments. Webpack
    namespace components (``webpack://my-app/src/…``) are kept — they are
    stable across runs and can be ignored via ``coverage.module_ignore``.
    """
    s = source
    if source_root:
        root = source_root if source_root.endswith("/") else source_root + "/"
        if not _SCHEME_RE.match(s) and not s.startswith("/"):
            s = root + s
    m = _SCHEME_RE.match(s)
    if m:
        s = s[m.end() :]
    s = s.lstrip("/")
    if s:
        s = posixpath.normpath(s)
        if s == ".":
            s = ""
    return s


def is_ignored(module: str, ignore: tuple[str, ...]) -> bool:
    """True when ``module`` falls under any ignore entry (prefix or any path level)."""
    for entry in ignore:
        clean = entry.rstrip("/")
        if not clean:
            continue
        if module == clean or module.startswith(clean + "/"):
            return True
        if f"/{clean}/" in f"/{module}":
            return True
    return False


def modules_for_script(
    executed_offsets: list[int],
    line_starts: list[int],
    map_obj: dict,
    ignore: tuple[str, ...] = (),
) -> set[str]:
    """Map executed character offsets to the set of executed source modules.

    Raises :class:`SourceMapUnusable` on structural problems (index maps,
    missing sources, malformed VLQ) so the caller can fall back to bundle
    granularity. Offsets that hit no mapping segment are silently skipped.
    """
    if not isinstance(map_obj, dict):
        raise SourceMapUnusable("map is not a JSON object")
    if "sections" in map_obj:
        raise SourceMapUnusable("index maps (sections) not supported")
    sources = map_obj.get("sources") or []
    if not sources:
        raise SourceMapUnusable("map has no sources")
    if not line_starts:
        raise SourceMapUnusable("missing line_starts")
    source_root = map_obj.get("sourceRoot") or ""
    normalized = [normalize_source_path(s or "", source_root) for s in sources]
    line_index = build_line_index(map_obj.get("mappings") or "")

    modules: set[str] = set()
    for offset in executed_offsets:
        line = bisect_right(line_starts, offset) - 1
        if line < 0 or line >= len(line_index):
            continue
        col = offset - line_starts[line]
        cols, idxs = line_index[line]
        i = bisect_right(cols, col) - 1
        if i < 0:
            continue
        idx = idxs[i]
        if 0 <= idx < len(normalized):
            module = normalized[idx]
            if module and not is_ignored(module, ignore):
                modules.add(module)
    return modules


def bundle_key(script_url: str) -> str:
    """Fallback module key when no usable map exists for a script."""
    path = urlsplit(script_url).path
    return f"bundle:{path or script_url}"


def load_map_for_entry(
    entry: dict,
    maps_dir: Path | None,
    sourcemap_dir: Path | None,
) -> dict | None:
    """Resolve the source map for one recorded script entry.

    Order: inline blob persisted by the recorder (``_maps/<sha256>.json.gz``)
    -> local-disk lookup under ``sourcemap_dir`` (by sourceMappingURL
    basename, then ``<script-basename>.map``; recursive, lexicographically
    first match) -> ``None`` (bundle fallback).
    """
    map_info = entry.get("map") or {}
    if map_info.get("kind") == "inline" and map_info.get("hash") and maps_dir is not None:
        blob = Path(maps_dir) / f"{map_info['hash']}.json.gz"
        if blob.is_file():
            try:
                return json.loads(gzip.decompress(blob.read_bytes()).decode("utf-8"))
            except (OSError, ValueError):
                return None

    if sourcemap_dir is None:
        return None
    sourcemap_dir = Path(sourcemap_dir)
    if not sourcemap_dir.is_dir():
        return None

    candidates: list[str] = []
    if map_info.get("kind") == "external" and map_info.get("url"):
        name = posixpath.basename(urlsplit(map_info["url"]).path)
        if name:
            candidates.append(name)
    script_name = posixpath.basename(urlsplit(entry.get("url") or "").path)
    if script_name:
        candidates.append(script_name + ".map")

    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        matches = sorted(sourcemap_dir.rglob(name), key=lambda p: p.as_posix())
        for match in matches:
            try:
                return json.loads(match.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return None
