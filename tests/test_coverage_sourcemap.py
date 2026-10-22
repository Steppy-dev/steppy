"""Sourcemap decoding: VLQ, offset->module mapping, degradation ladder.

The handcrafted fixture maps generated code
``function a(){return 1}function b(){return 2}`` (b starts at offset 23)
onto two sources via mappings ``AAAA,uBCAA`` ([0,0,0,0] then [+23,+1,0,0]).
"""

import gzip
import json

import pytest

from steppy.coverage.sourcemap import (
    SourceMapUnusable,
    build_line_index,
    bundle_key,
    is_ignored,
    load_map_for_entry,
    modules_for_script,
    normalize_source_path,
)

_GENERATED = "function a(){return 1}function b(){return 2}"
_MAP = {
    "version": 3,
    "sources": ["webpack:///./src/a.ts", "webpack:///./src/b.ts"],
    "names": [],
    "mappings": "AAAA,uBCAA",
}
_LINE_STARTS = [0]


def test_build_line_index_decodes_vlq():
    cols, idxs = build_line_index(_MAP["mappings"])[0]
    assert cols == [0, 23]
    assert idxs == [0, 1]


@pytest.mark.parametrize(
    ("offsets", "expected"),
    [
        ([0], {"src/a.ts"}),
        ([23], {"src/b.ts"}),
        ([0, 23], {"src/a.ts", "src/b.ts"}),
        ([10], {"src/a.ts"}),  # mid-function offset floor-maps to its segment
    ],
)
def test_modules_for_script(offsets, expected):
    assert modules_for_script(offsets, _LINE_STARTS, _MAP) == expected


def test_src_idx_accumulates_across_lines():
    # "function a(){}\nfunction b(){}" — line 2 starts at offset 15.
    multi = {
        "version": 3,
        "sources": ["src/a.ts", "src/b.ts"],
        "mappings": "AAAA;ACAA",  # line 2: [0, +1, 0, 0]
    }
    assert modules_for_script([0], [0, 15], multi) == {"src/a.ts"}
    assert modules_for_script([15], [0, 15], multi) == {"src/b.ts"}


def test_offset_beyond_mapped_lines_is_skipped():
    single_line_map = {"version": 3, "sources": ["src/a.ts"], "mappings": "AAAA"}
    # line_starts says offset 20 is on generated line 2, which the map lacks.
    assert modules_for_script([20], [0, 15], single_line_map) == set()


def test_ignore_filter():
    map_obj = {
        "version": 3,
        "sources": ["node_modules/lib/index.js", "src/app/app.ts"],
        "mappings": "AAAA,uBCAA",
    }
    out = modules_for_script([0, 23], _LINE_STARTS, map_obj, ignore=("node_modules/",))
    assert out == {"src/app/app.ts"}


@pytest.mark.parametrize(
    ("map_obj", "match"),
    [
        ({"sections": [], "sources": ["x"]}, "index maps"),
        ({"version": 3, "sources": []}, "no sources"),
        ({"version": 3, "sources": ["a"], "mappings": "!!"}, "invalid VLQ"),
        ("not-a-dict", "not a JSON object"),
    ],
)
def test_unusable_maps_raise(map_obj, match):
    with pytest.raises(SourceMapUnusable, match=match):
        modules_for_script([0], _LINE_STARTS, map_obj)


def test_missing_line_starts_raises():
    with pytest.raises(SourceMapUnusable, match="line_starts"):
        modules_for_script([0], [], _MAP)


@pytest.mark.parametrize(
    ("source", "root", "expected"),
    [
        ("webpack:///./src/a.ts", "", "src/a.ts"),
        ("webpack://my-app/src/x.ts", "", "my-app/src/x.ts"),
        ("src/x.ts", "webpack:///", "src/x.ts"),
        ("./src/y.ts", "", "src/y.ts"),
        ("/abs/z.ts", "", "abs/z.ts"),
        ("ng:///AppModule/m.ts", "", "AppModule/m.ts"),
    ],
)
def test_normalize_source_path(source, root, expected):
    assert normalize_source_path(source, root) == expected


def test_is_ignored_at_any_depth():
    assert is_ignored("node_modules/lib/x.js", ("node_modules/",))
    assert is_ignored("my-app/node_modules/lib/x.js", ("node_modules/",))
    assert not is_ignored("src/app/app.ts", ("node_modules/", "webpack/"))


def test_bundle_key():
    assert bundle_key("http://localhost:4200/main-ABC123.js") == "bundle:/main-ABC123.js"


def test_load_map_inline_blob(tmp_path):
    maps_dir = tmp_path / "_maps"
    maps_dir.mkdir()
    (maps_dir / "deadbeef.json.gz").write_bytes(gzip.compress(json.dumps(_MAP).encode()))
    entry = {"url": "http://x/main.js", "map": {"kind": "inline", "hash": "deadbeef"}}
    assert load_map_for_entry(entry, maps_dir, None) == _MAP


def test_load_map_corrupt_inline_blob_returns_none(tmp_path):
    maps_dir = tmp_path / "_maps"
    maps_dir.mkdir()
    (maps_dir / "bad.json.gz").write_bytes(b"not gzip")
    entry = {"url": "http://x/main.js", "map": {"kind": "inline", "hash": "bad"}}
    assert load_map_for_entry(entry, maps_dir, None) is None


def test_load_map_from_disk_by_sourcemapping_url(tmp_path):
    dist = tmp_path / "dist" / "browser"
    dist.mkdir(parents=True)
    (dist / "main-ABC.js.map").write_text(json.dumps(_MAP), encoding="utf-8")
    entry = {
        "url": "http://localhost:4200/main-ABC.js",
        "map": {"kind": "external", "url": "main-ABC.js.map"},
    }
    assert load_map_for_entry(entry, None, tmp_path / "dist") == _MAP


def test_load_map_from_disk_by_script_basename(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "chunk-7.js.map").write_text(json.dumps(_MAP), encoding="utf-8")
    # No sourceMappingURL recorded — fall back to <script-basename>.map.
    entry = {"url": "http://localhost:4200/chunk-7.js", "map": {"kind": "none"}}
    assert load_map_for_entry(entry, None, dist) == _MAP


def test_load_map_missing_everywhere(tmp_path):
    entry = {"url": "http://x/main.js", "map": {"kind": "none"}}
    assert load_map_for_entry(entry, tmp_path, tmp_path) is None
