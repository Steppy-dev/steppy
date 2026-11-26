"""Click hotspots: read from the run's own sidecars, and never an error.

The contract's ``captures`` are what Steppy Cloud computes from the same sidecars, so
the shape is pinned field by field. Everything malformed gives a screenshot without a
box: a bundle may come from anywhere, and a report of it must still be made.
"""

import io
import json
import struct
import tarfile
from pathlib import Path

import pytest

from steppy.bundle.archive import read_members
from steppy.contracts.bundle import BundleFile, BundleManifest, BundleRun, BundleTest
from steppy.contracts.report import ReportSourceInfo
from steppy.report.assets import image_sizes, plan_assets, png_size
from steppy.report.build import build_document, build_report
from steppy.report.hotspots import MAX_BOXES, MAX_SIDECAR_BYTES, Hotspot, pair, parse, read
from steppy.report.model import build_model
from steppy.report.source import open_source

from .test_bundle import RUN_ID, build_project, capture_rows, step_captures

RUN = BundleRun(run_id="r1", created_at="2026-08-14T10:00:00Z", steppy_version="0.10.0")
BOX = {"x": 10, "y": 20.005, "width": 30, "height": 40}


def _sidecar(**kw) -> bytes:
    payload = {
        "step": 1,
        "screenshot": "step-01.png",
        "action": "click",
        "viewport": {"width": 1280, "height": 800},
        "device_scale_factor": 2,
        "target": {"kind": "single", "selector": "#go", "bbox": BOX},
        **kw,
    }
    return json.dumps(payload).encode()


# ── parsing ─────────────────────────────────────────────────────────────────


def test_a_single_target():
    hotspot = parse(_sidecar())
    assert hotspot is not None
    assert hotspot.fields() == {
        "viewport": {"width": 1280.0, "height": 800.0},
        "device_scale_factor": 2.0,
        "boxes": [{"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0}],
    }


def test_a_form_has_one_box_per_field_found():
    fields = [{"selector": "#a", "bbox": BOX}, {"selector": "#b", "bbox": None}, {"bbox": {**BOX, "x": 99}}, "junk"]
    hotspot = parse(_sidecar(target={"kind": "multi", "fields": fields}))
    assert hotspot is not None and [b["x"] for b in hotspot.boxes] == [10.0, 99.0]


def test_no_target_is_a_capture_without_a_box():
    hotspot = parse(_sidecar(target=None))
    assert hotspot == Hotspot(device_scale_factor=2.0, viewport={"width": 1280.0, "height": 800.0})


def test_a_missing_scale_factor_is_one():
    payload = json.loads(_sidecar())
    del payload["device_scale_factor"]
    hotspot = parse(json.dumps(payload).encode())
    assert hotspot is not None and hotspot.device_scale_factor == 1.0


def test_a_target_kind_nobody_knows_gives_no_box():
    hotspot = parse(_sidecar(target={"kind": "polygon", "points": [1, 2]}))
    assert hotspot is not None and hotspot.boxes == ()


def test_the_boxes_are_capped():
    hotspot = parse(_sidecar(target={"kind": "multi", "fields": [{"bbox": BOX}] * (MAX_BOXES + 50)}))
    assert hotspot is not None and len(hotspot.boxes) == MAX_BOXES


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{",
        b"[]",
        b"null",
        b'"text"',
        b"\xff\xfe\x00",
        b"[" * 60_000,  # RecursionError in the C scanner
        b'{"device_scale_factor": ' + b"9" * 400 + b"}",  # OverflowError
        b'{"device_scale_factor": ' + b"9" * 5000 + b"}",  # past the int digit limit
        b" " * (MAX_SIDECAR_BYTES + 1),
        _sidecar(device_scale_factor=0),
        _sidecar(device_scale_factor=-1),
        _sidecar(device_scale_factor="2"),
        _sidecar(device_scale_factor=True),
        b'{"device_scale_factor": NaN, "target": null}',
        b'{"device_scale_factor": Infinity, "target": null}',
        _sidecar(target="x"),
        _sidecar(target=[1]),
        _sidecar(target={"kind": "multi", "fields": {"a": 1}}),
    ],
    ids=lambda d: repr(d[:24]),
)
def test_what_is_not_a_sidecar_is_none_and_never_raises(data):
    assert parse(data) is None


@pytest.mark.parametrize(
    "box",
    [
        None,
        [],
        "10,20",
        {},
        {**BOX, "width": 0},
        {**BOX, "height": -1},
        {**BOX, "x": None},
        {**BOX, "x": "10"},
        {**BOX, "y": True},
        {**BOX, "x": float("nan")},
        {**BOX, "x": float("inf")},
    ],
)
def test_a_box_nobody_can_draw_is_dropped_and_the_sidecar_kept(box):
    hotspot = parse(_sidecar(target={"kind": "single", "bbox": box}))
    assert hotspot is not None and hotspot.boxes == ()


@pytest.mark.parametrize("viewport", [None, [], {"width": 0, "height": 1}, {"width": "a", "height": 1}, 5])
def test_an_invalid_viewport_is_dropped_and_the_box_kept(viewport):
    hotspot = parse(_sidecar(viewport=viewport))
    assert hotspot is not None and hotspot.viewport is None and len(hotspot.boxes) == 1


# ── pairing ─────────────────────────────────────────────────────────────────


def _file(kind, step=1, *, scenario="pay", locale="fr", layout="mobile", frame=None, digest="0" * 64, size=10):
    stem = f"step-{step:02d}" + (f"-{frame}" if frame else "")
    suffix = "json" if kind == "sidecar" else "png"
    return BundleFile(
        path=f"output/screenshots/{scenario}/{locale}/{layout}/{stem}.{suffix}",
        kind=kind,
        sha256=digest,
        size=size,
        scenario=scenario,
        locale=locale,
        layout=layout,
        step=step,
        frame=frame,
    )


def _manifest(*files):
    test = BundleTest(scenario="pay", nodeid="n", outcome="failed", locale="fr", layout="mobile")
    return BundleManifest(steppy_bundle=1, run=RUN, tests=[test], files=list(files))


def test_a_sidecar_belongs_to_the_frame_it_was_written_for():
    files = [_file(kind, 1, frame=frame) for frame in ("a", "b") for kind in ("screenshot", "sidecar")]
    files.append(_file("screenshot", 2))
    manifest = _manifest(*files)
    shots = [f.path for f in files if f.kind == "screenshot"]
    assert pair(manifest, shots) == {p: p.replace(".png", ".json") for p in shots[:2]}
    assert list(pair(manifest, shots[:1])) == shots[:1]  # only the ones asked for


class _Source:
    def __init__(self, manifest, texts):
        self.manifest, self.texts, self.asked = manifest, texts, None

    def fetch(self, *, texts, max_bytes):
        self.asked = (texts, max_bytes)
        return {k: v for k, v in self.texts.items() if k in texts}, ["a warning nobody can act on"]


def test_reading_asks_for_the_paired_sidecars_only_and_bounds_their_size():
    shot, side = _file("screenshot"), _file("sidecar")
    source = _Source(_manifest(shot, side), {side.path: _sidecar()})
    assert list(read(source, [shot.path])) == [shot.path]
    assert source.asked == ({side.path}, MAX_SIDECAR_BYTES)


def test_a_missing_or_malformed_sidecar_leaves_its_screenshot_alone():
    shot, side = _file("screenshot"), _file("sidecar")
    assert read(_Source(_manifest(shot, side), {}), [shot.path]) == {}
    assert read(_Source(_manifest(shot, side), {side.path: b"{"}), [shot.path]) == {}
    assert read(_Source(_manifest(shot), {}), [shot.path]) == {}


def test_a_local_sidecar_over_the_limit_is_not_read(tmp_path: Path):
    project = _captures(build_project(tmp_path))
    source = open_source(None, project_root=project)
    side = next(f.path for f in source.manifest.files if f.kind == "sidecar")
    on_disk = project / side
    on_disk.write_bytes(_sidecar() + b" " * MAX_SIDECAR_BYTES)
    assert source.fetch(texts={side}, max_bytes=MAX_SIDECAR_BYTES) == ({}, [])
    assert len(source.fetch(texts={side})[0][side]) > MAX_SIDECAR_BYTES


def test_an_archived_sidecar_over_the_limit_is_not_read_whatever_the_manifest_says(tmp_path: Path):
    archive = tmp_path / "b.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in (("small.json", b"{}"), ("big.json", b" " * (MAX_SIDECAR_BYTES + 1))):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    fetched, warnings = read_members(archive, texts={"small.json", "big.json"}, max_bytes=MAX_SIDECAR_BYTES)
    assert (fetched, warnings) == ({"small.json": b"{}"}, [])


# ── the contract ────────────────────────────────────────────────────────────


def _events(nodeid="n"):
    from steppy.bundle.results import steps_from_records

    return steps_from_records(
        [
            {"kind": "test-start", "ts": 1.0, "nodeid": nodeid},
            {"kind": "step-start", "ts": 1.0, "nodeid": nodeid, "step": 1, "title": "Fill", "action": "fill-form"},
            {"kind": "step-end", "ts": 2.0, "nodeid": nodeid, "step": 1},
            {"kind": "test-end", "ts": 2.0, "nodeid": nodeid, "outcome": "failed", "duration_ms": 1000},
        ]
    )


def test_a_step_has_one_capture_per_screenshot_in_the_same_order():
    files = [_file("screenshot", frame=frame, digest=frame * 64) for frame in ("b", "a")]
    assets = {f.path: f"assets/{f.frame}.png" for f in files}
    hotspots = {
        files[0].path: Hotspot(device_scale_factor=3.0, boxes=({"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0},))
    }
    model = build_model(
        manifest=_manifest(*files),
        source=ReportSourceInfo(kind="tree"),
        attempts=_events(),
        assets=assets,
        hotspots=hotspots,
    )
    step = model.scenarios[0].variants[0].steps[0]
    assert step.screenshots == ["assets/a.png", "assets/b.png"]
    assert [c.model_dump(mode="json") for c in step.captures] == [
        {"url": "assets/a.png", "viewport": None, "device_scale_factor": None, "boxes": []},
        {
            "url": "assets/b.png",
            "viewport": None,
            "device_scale_factor": 3.0,
            "boxes": [{"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}],
        },
    ]


def test_byte_identical_captures_share_a_url_and_keep_their_own_boxes():
    files = [_file("screenshot", frame=frame) for frame in ("a", "b")]
    boxes = [({"x": x, "y": 0.0, "width": 1.0, "height": 1.0},) for x in (1.0, 2.0)]
    model = build_model(
        manifest=_manifest(*files),
        source=ReportSourceInfo(kind="tree"),
        attempts=_events(),
        assets={f.path: "assets/same.png" for f in files},
        hotspots={f.path: Hotspot(boxes=b) for f, b in zip(files, boxes)},
    )
    captures = model.scenarios[0].variants[0].steps[0].captures
    assert [c.boxes[0].x for c in captures] == [1.0, 2.0]


def test_a_report_made_before_captures_still_reads():
    from steppy.contracts.report import parse_report

    payload = {
        "steppy_report": 1,
        "source": {"kind": "tree"},
        "run": {"run_id": "r1", "created_at": "x", "steppy_version": "0.9.2"},
        "scenarios": [{"slug": "a", "variants": [{"nodeid": "n", "steps": [{"step": 1, "screenshots": ["a.png"]}]}]}],
    }
    assert parse_report(payload).scenarios[0].variants[0].steps[0].captures == []


# ── the whole way ───────────────────────────────────────────────────────────


def _png(width: int, height: int) -> bytes:
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = b"\0" + b"\x20\x40\x60" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def _captures(project: Path) -> Path:
    """Real PNGs and sidecars over the fixture's own captures, recorded as the run's."""
    for png in sorted((project / "output" / "screenshots").rglob("step-*.png")):
        png.write_bytes(_png(200, 100))
        png.with_suffix(".json").write_bytes(_sidecar())
    return _record(project)


def _record(project: Path) -> Path:
    """Rewrite the run's capture records for what is on disk now, or the files are not the run's."""
    events = project / "output" / "events" / RUN_ID / "order-lifecycle__fr-mobile.jsonl"
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line]
    rows = [r for r in rows if r["kind"] != "capture"] + capture_rows(project, *step_captures(project))
    events.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
    return project


def test_the_site_draws_the_box_and_report_json_carries_it(tmp_path: Path):
    project = _captures(build_project(tmp_path))
    result = build_report(project_root=project, site_dir=tmp_path / "site", screenshots="all")
    captures = [c for s in result.model.scenarios for v in s.variants for step in v.steps for c in step.captures]
    assert captures and all(c.boxes and c.device_scale_factor == 2.0 for c in captures)
    pages = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "site" / "scenarios").glob("*.html"))
    # 200 x 100 at a scale of 2 is 100 x 50 CSS pixels: the box at 10, 20 is at 10 % and 40 %.
    assert 'style="left:10%;top:40%;width:30%;height:80%"' in pages
    assert 'width="200" height="100"' in pages


@pytest.mark.parametrize(
    "data", [b"{", b"[" * 60_000, b" " * (MAX_SIDECAR_BYTES + 1), None], ids=["bad", "deep", "big", "gone"]
)
def test_a_report_is_made_whatever_a_sidecar_holds(tmp_path: Path, data):
    project = _captures(build_project(tmp_path))
    for side in (project / "output" / "screenshots").rglob("step-*.json"):
        side.unlink() if data is None else side.write_bytes(data)
    _record(project)
    result = build_report(project_root=project, site_dir=tmp_path / "site", screenshots="all")
    pages = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "site" / "scenarios").glob("*.html"))
    assert "step-01.png" in pages
    assert "steppy-mark-box" not in pages
    assert not any("step-01.json" in w for w in result.warnings)


def test_the_uploads_report_names_each_capture_by_its_manifest_path(tmp_path: Path):
    project = _captures(build_project(tmp_path))
    source = open_source(None, project_root=project)
    document = build_document(source, project_root=project)
    paths = {f.path for f in source.manifest.files if f.kind == "screenshot"}
    steps = [step for s in document.scenarios for v in s.variants for step in v.steps if step.screenshots]
    assert steps
    for step in steps:
        assert [c.url for c in step.captures] == step.screenshots
        assert set(step.screenshots) <= paths
        assert all(c.boxes for c in step.captures)


# ── image sizes ─────────────────────────────────────────────────────────────


def test_the_size_of_a_png_comes_from_its_header(tmp_path: Path):
    image = tmp_path / "a.png"
    image.write_bytes(_png(390, 844))
    assert png_size(image) == (390, 844)


@pytest.mark.parametrize("data", [b"", b"\x89PNG", b"GIF89a" + b"\0" * 40, b"\x89PNG\r\n\x1a\n" + b"\0" * 16])
def test_anything_else_has_no_size(tmp_path: Path, data):
    image = tmp_path / "a.png"
    image.write_bytes(data)
    assert png_size(image) is None
    assert png_size(tmp_path / "missing.png") is None


def test_sizes_are_keyed_by_the_url_the_page_uses(tmp_path: Path):
    shot = _file("screenshot")
    plan = plan_assets(_manifest(shot), site_dir=tmp_path, policy="all")
    target = plan.into[shot.path]
    target.parent.mkdir(parents=True)
    target.write_bytes(_png(10, 20))
    assert image_sizes(plan) == {"assets/pay/fr-mobile/step-01.png": (10, 20)}
