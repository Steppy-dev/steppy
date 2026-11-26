"""A bundle, a run record and a coverage record may come from another machine.

Each one names files. These tests hand every command that reads one a hostile copy
and check the same two things: nothing outside is read or written, and the refusal is
said out loud. They use the commands' own entry points, never the rules behind them
(``tests/test_safe_paths.py``), so they fail when a call site forgets to ask.
"""

import base64
import gzip
import hashlib
import io
import json
import os
import tarfile
import tempfile
from pathlib import Path

import pytest

from steppy.bundle import BundleError, create_bundle, inspect_bundle, verify_bundle
from steppy.bundle.create import plan_bundle
from steppy.report.build import build_document, build_report
from steppy.report.source import ReportSourceError, open_source

from .test_bundle import build_project

SECRET = b"TOP-SECRET-0123456789"
NODEID = "tests/e2e/test_pay.py::test_pay[fr-mobile]"


def _link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt" and target.is_dir():
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("this system lets a test make no link")


def _file(path: str, kind: str, **kw) -> dict:
    return {"path": path, "kind": kind, "sha256": kw.pop("sha256", "0" * 64), "size": kw.pop("size", 10), **kw}


def _shot(path: str, step: int = 1, **kw) -> dict:
    digest = hashlib.sha256(path.encode()).hexdigest()
    where = {"scenario": "pay", "locale": "fr", "layout": "mobile", "step": step}
    return _file(path, "screenshot", sha256=digest, **{**where, **kw})


def _manifest(files: list, run_id: str = "20261125T200000Z-abcdef") -> dict:
    return {
        "steppy_bundle": 1,
        "run": {"run_id": run_id, "created_at": "2026-11-25T20:00:00Z", "steppy_version": "0.10.0"},
        "tests": [
            {
                "scenario": "pay",
                "nodeid": NODEID,
                "outcome": "failed",
                "locale": "fr",
                "layout": "mobile",
                "fail_tail_b64": base64.b64encode(b"E   AssertionError: boom").decode(),
            }
        ],
        "files": files,
    }


def _events() -> bytes:
    rows = [
        {"kind": "test-start", "ts": 1.0, "nodeid": NODEID},
        {"kind": "step-start", "ts": 1.0, "nodeid": NODEID, "step": 1, "title": "Pay", "action": "click"},
        {"kind": "test-end", "ts": 2.0, "nodeid": NODEID, "outcome": "failed", "duration_ms": 1000},
    ]
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


class Lair:
    """An unpacked bundle, and a directory beside it that nothing may reach."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.root = tmp_path / "bundle"
        self.outside = tmp_path / "outside"
        self.project = tmp_path / "project"
        for directory in (self.root, self.outside, self.project):
            directory.mkdir()
        (self.outside / "secret.png").write_bytes(SECRET)
        (self.project / "config.yaml").write_text("apps: {}\n", encoding="utf-8")
        self.site = tmp_path / "site"

    def unpacked(self, files: list, members: dict | None = None, run_id: str = "20261125T200000Z-abcdef") -> Path:
        manifest = _manifest([_file("events/pay.jsonl", "events"), *files], run_id)
        for name, data in {"events/pay.jsonl": _events(), **(members or {})}.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (self.root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return self.root

    def archive(self, files: list, members: dict | None = None, run_id: str = "20261125T200000Z-abcdef") -> Path:
        path = self.tmp / "bundle.tar.gz"
        manifest = _manifest([_file("events/pay.jsonl", "events"), *files], run_id)
        everything = {"manifest.json": json.dumps(manifest).encode(), "events/pay.jsonl": _events(), **(members or {})}
        with tarfile.open(path, "w:gz") as tar:
            for name, data in everything.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    def report(self, bundle: Path):
        return build_report(project_root=self.project, bundle=bundle, site_dir=self.site, screenshots="all")

    def site_bytes(self) -> bytes:
        return b"".join(p.read_bytes() for p in sorted(self.site.rglob("*")) if p.is_file())


@pytest.fixture
def lair(tmp_path: Path) -> Lair:
    return Lair(tmp_path)


def _hostile_names(lair: Lair) -> list[str]:
    absolute = (lair.outside / "secret.png").resolve()
    return [
        "../outside/secret.png",
        "output/../../outside/secret.png",
        "output/screenshots/../../../outside/secret.png",
        "..\\outside\\secret.png",
        "output\\..\\..\\outside\\secret.png",
        absolute.as_posix(),
        str(absolute),
        f"//{absolute.as_posix().lstrip('/')}",
        f"\\\\localhost\\{str(absolute).replace(':', '$')}",
        f"\\\\?\\{absolute}",
        "../outside/./secret.png",
        "../outside//secret.png",
    ]


def _refused(result) -> str:
    said = [w for w in result.model.warnings if "may leave the bundle" in w]
    assert len(said) == 1, result.model.warnings
    assert said[0] in result.warnings  # printed by the command, not only on the page
    return said[0]


# ── steppy report <unpacked directory> ──────────────────────────────────────


@pytest.mark.parametrize("kind", ["screenshot", "failure_screenshot"])
def test_a_screenshot_named_outside_the_bundle_is_never_copied(lair: Lair, kind):
    names = _hostile_names(lair)
    files = [_shot(name, step=i) if kind == "screenshot" else _failure(name) for i, name in enumerate(names, 1)]
    result = lair.report(lair.unpacked(files))
    assert SECRET not in lair.site_bytes()
    assert not list(lair.site.rglob("*.png"))
    assert f"refused {len(set(names))} file(s)" in _refused(result)
    assert (lair.site / "index.html").is_file()  # the rest of the run is still worth reading


def _failure(name: str) -> dict:
    entry = _shot(name)
    entry.update({"kind": "failure_screenshot", "step": None})
    return entry


@pytest.mark.parametrize("what", ["file", "directory"])
def test_a_link_out_of_the_bundle_is_never_followed(lair: Lair, what):
    if what == "file":
        name = "output/screenshots/pay/fr/mobile/step-01.png"
        _link(lair.root / name, lair.outside / "secret.png")
    else:
        name = "output/screenshots/secret.png"
        _link(lair.root / "output" / "screenshots", lair.outside)
    result = lair.report(lair.unpacked([_shot(name)]))
    assert SECRET not in lair.site_bytes()
    assert "a link takes it outside" in _refused(result)


def test_a_link_that_stays_inside_is_read(lair: Lair):
    root = lair.unpacked([_shot("output/alias/step-01.png")], {"output/real/step-01.png": b"inside"})
    _link(root / "output" / "alias", root / "output" / "real")
    result = lair.report(root)
    assert b"inside" in lair.site_bytes()
    assert not any("may leave the bundle" in w for w in result.warnings)


@pytest.mark.parametrize("kind", ["events", "sidecar", "coverage_gaps"])
def test_text_named_outside_the_bundle_is_never_read(lair: Lair, kind):
    """Not only images: what a report reads to show (steps, targets, coverage)."""
    stolen = {
        "events": _events().replace(b'"Pay"', b'"STOLEN-STEP"'),
        "sidecar": json.dumps(
            {"target": {"kind": "single", "bbox": {"x": 4242, "y": 1, "width": 2, "height": 3}}}
        ).encode(),
        "coverage_gaps": json.dumps({"counts": {"routes": {"declared": 4242}}, "e2e_surface": {}}).encode(),
    }[kind]
    (lair.outside / "stolen").write_bytes(stolen)
    shot = "output/screenshots/pay/fr/mobile/step-01.png"
    extra = {"scenario": "pay", "locale": "fr", "layout": "mobile", "step": 1} if kind == "sidecar" else {}
    root = lair.unpacked([_shot(shot), _file("../outside/stolen", kind, **extra)], {shot: b"png"})
    result = lair.report(root)
    assert b"STOLEN-STEP" not in lair.site_bytes() and b"4242" not in lair.site_bytes()
    _refused(result)


def test_a_manifest_that_is_a_link_out_is_an_error(lair: Lair):
    (lair.outside / "manifest.json").write_text(json.dumps(_manifest([])), encoding="utf-8")
    _link(lair.root / "manifest.json", lair.outside / "manifest.json")
    with pytest.raises(ReportSourceError, match="refusing to read"):
        open_source(lair.root, project_root=lair.project)


def test_an_ordinary_bundle_is_refused_nothing(lair: Lair):
    shot = "output/screenshots/mon scénario/fr/mobile/step-01.png"
    for bundle in (lair.unpacked([_shot(shot)], {shot: b"png"}), lair.archive([_shot(shot)], {shot: b"png"})):
        result = lair.report(bundle)
        assert not any("refused" in w for w in result.warnings)
        assert b"png" in lair.site_bytes()


def test_the_warning_names_a_few_and_counts_the_rest_without_control_characters(lair: Lair):
    files = [_shot(f"../{i}\x1b[31m.png", step=i + 1) for i in range(9)]
    said = _refused(lair.report(lair.unpacked(files)))
    assert "refused 9 file(s)" in said and "and 4 more" in said
    assert "\x1b" not in said


def test_a_file_nobody_asks_for_is_no_alarm_unless_its_name_leads_out(lair: Lair):
    """A project's own log, named by its time of day, is ordinary where it was written."""
    ordinary = [_file("output/logs/20:00:00.log", "other"), _file("output/a\\b.txt", "other")]
    assert not any("refused" in w for w in lair.report(lair.archive(ordinary)).warnings)
    assert "refused 1 file(s)" in _refused(lair.report(lair.archive([*ordinary, _file("../x.log", "other")])))


@pytest.mark.skipif(os.name != "nt", reason="a device is a Windows name")
@pytest.mark.parametrize("name", ["output/CON", "nul.png", "output/COM1.png"])
def test_a_device_is_never_opened(lair: Lair, name):
    assert "reserves" in _refused(lair.report(lair.unpacked([_shot(name)])))


# ── steppy report <archive> ─────────────────────────────────────────────────


def test_an_archive_is_judged_like_its_unpacked_directory(lair: Lair):
    """Its bytes are its own, so nothing leaks; the two must still make the same report."""
    names = ["../outside/secret.png", "/abs.png", "C:\\x.png", "..\\x.png"]
    files = [_shot(name, step=i) for i, name in enumerate(names, 1)]
    result = lair.report(lair.archive(files, {name: b"from the archive" for name in names}))
    assert "refused 4 file(s)" in _refused(result)
    assert b"from the archive" not in lair.site_bytes()


RECORD = {
    "steppy_coverage_record": 1,
    "run_id": "20261125T200000Z-abcdef",
    "scenario": "pay",
    "role": "editor",
    "outcome": "failed",
    "nav_events": [{"u": "http://localhost:4200/checkout", "k": "nav", "t": 1.0}],
    "spa_events": [],
    "requests": [],
    "requests_dropped": 0,
    "js_coverage": {"ok": False, "reason": "test fixture", "scripts": []},
}


def _visits(path: str) -> bytes:
    return json.dumps({**RECORD, "nav_events": [{"u": f"http://localhost:4200{path}", "k": "nav", "t": 1.0}]}).encode()


@pytest.mark.parametrize("shape", ["archive", "unpacked"])
@pytest.mark.parametrize(
    "name",
    [
        "output/coverage/raw/../../../../pwned.json",
        "output/coverage/raw/x/../../../../../pwned.json",
        "output/coverage/raw/..\\..\\..\\..\\pwned.json/x/y",
        "output/coverage/raw/run/..\\..\\..\\..\\..\\x/pwned.json",
        "raw/run/../pwned.json",
        "raw/C:/x/pwned.json",
    ],
)
def test_a_coverage_record_is_never_written_outside_its_temporary_tree(lair: Lair, monkeypatch, shape, name):
    """The records of a bundle are extracted to be merged: a path here is written to."""
    sandbox = lair.tmp / "a" / "b" / "temp"
    sandbox.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(sandbox))
    before = {p for p in lair.tmp.rglob("*")}
    entry = _file(name, "coverage_record", scenario="pay")
    if shape == "archive":
        bundle = lair.archive([entry], {name: json.dumps(RECORD).encode()})
    else:
        bundle = lair.unpacked([entry])
    result = lair.report(bundle)
    written = {p for p in lair.tmp.rglob("*") if p not in before}
    mine = {lair.site, lair.root, lair.tmp / "bundle.tar.gz"}
    strays = [p for p in written if not any(p == m or p.is_relative_to(m) for m in mine)]
    assert strays == []
    assert result.model.coverage.tier == "none"
    assert any("left out 1 record" in w for w in result.model.coverage.warnings) or _refused(result)


def test_ordinary_coverage_records_are_still_merged_whatever_the_run_is_called(lair: Lair):
    """The manifest's run id may differ from the records' own directory, legitimately."""
    name = "output/coverage/raw/20261125T190000Z-ffffff/pay/editor_page__fr-mobile.json"
    result = lair.report(lair.archive([_file(name, "coverage_record", scenario="pay")], {name: _visits("/checkout")}))
    assert result.model.coverage.tier == "records"
    assert b"/checkout" in (lair.site / "report.json").read_bytes()


@pytest.mark.parametrize("how", ["relative", "absolute"])
def test_a_run_id_never_names_the_directory_coverage_is_read_from(lair: Lair, monkeypatch, how):
    """Another project's records, one directory over, must not be merged into this report."""
    stolen = lair.outside / "raw-run" / "pay"
    stolen.mkdir(parents=True)
    (stolen / "r.json").write_bytes(_visits("/stolen-route"))
    (lair.tmp / "temp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(lair.tmp / "temp"))
    # From <tmp>/temp/<random>/coverage/raw.
    run_id = str(stolen.parent.resolve()) if how == "absolute" else "../../../../outside/raw-run"
    name = "output/coverage/raw/run/pay/r.json"
    bundle = lair.archive([_file(name, "coverage_record", scenario="pay")], {name: _visits("/mine")}, run_id=run_id)
    lair.report(bundle)
    assert b"stolen-route" not in lair.site_bytes()


# ── coverage records, merged ────────────────────────────────────────────────


def test_a_source_maps_digest_is_never_a_path(tmp_path: Path):
    from steppy.coverage.sourcemap import load_map_for_entry

    maps = tmp_path / "run" / "_maps"
    maps.mkdir(parents=True)
    (tmp_path / "outside.json.gz").write_bytes(gzip.compress(json.dumps({"sources": ["STOLEN"]}).encode()))
    for digest in ("../../outside", "..\\..\\outside", str(tmp_path / "outside")):
        entry = {"url": "http://app/main.js", "map": {"kind": "inline", "hash": digest}}
        assert load_map_for_entry(entry, maps, None) is None, digest
    (maps / "abc.json.gz").write_bytes(gzip.compress(json.dumps({"sources": ["mine"]}).encode()))
    entry = {"url": "http://app/main.js", "map": {"kind": "inline", "hash": "abc"}}
    assert load_map_for_entry(entry, maps, None) == {"sources": ["mine"]}


def test_a_source_maps_name_is_never_a_pattern_or_a_path(tmp_path: Path):
    from steppy.coverage.sourcemap import load_map_for_entry

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "other.js.map").write_text(json.dumps({"sources": ["OTHER"]}), encoding="utf-8")
    (tmp_path / "secret.map").write_text(json.dumps({"sources": ["STOLEN"]}), encoding="utf-8")
    for url in ("http://app/*.map", "http://app/..\\secret.map", "http://app/**", "http://app/[a-z]*.js.map"):
        entry = {"url": "http://app/none.js", "map": {"kind": "external", "url": url}}
        assert load_map_for_entry(entry, None, dist) is None, url
    entry = {"url": "http://app/other.js", "map": {"kind": "external", "url": "http://app/other.js.map"}}
    assert load_map_for_entry(entry, None, dist) == {"sources": ["OTHER"]}


def test_a_scenario_id_never_leaves_the_scenarios_directory(tmp_path: Path):
    from steppy.coverage.merge import count_scenario_asserts

    text = "---\ntitle: T\n---\n\n### 1. See\n- role: editor\n- action: assert\n- expect: visible(#x)\n"
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "pay.md").write_text(text, encoding="utf-8")
    (tmp_path / "outside.md").write_text(text, encoding="utf-8")
    assert count_scenario_asserts(tmp_path / "scenarios", "pay") == 1
    for scenario in ("../outside", "..\\outside", str(tmp_path / "outside")):
        assert count_scenario_asserts(tmp_path / "scenarios", scenario) == 0, scenario


@pytest.mark.parametrize("run_id", ["../elsewhere", "..\\elsewhere", "/abs", "C:\\x", "a/b", "..", "a\nb"])
def test_a_coverage_run_id_is_one_plain_name(tmp_path: Path, run_id):
    from steppy.coverage.merge import CoverageSettings, MergeError, merge_run

    (tmp_path / "elsewhere" / "pay").mkdir(parents=True)
    (tmp_path / "elsewhere" / "pay" / "r.json").write_text(json.dumps(RECORD), encoding="utf-8")
    settings = CoverageSettings(store_path=tmp_path / "map.db", records_dir=tmp_path / "cov")
    with pytest.raises(MergeError, match="not a coverage run id"):
        merge_run(settings, run_id)


# ── the live tree: an unpacked bundle is one too ────────────────────────────

BAD_RUN_IDS = ["../../outside", "..\\..\\outside", "/abs", "C:\\x", "x/../../../evil", "..", "a\nb", "\\\\host\\s"]


def _sidecar(project: Path, run_id: str) -> None:
    (project / "output" / ".steppy-run.json").write_text(
        json.dumps({"steppy_run": 1, "run_id": run_id, "spec": {}}), encoding="utf-8"
    )


@pytest.mark.parametrize("run_id", BAD_RUN_IDS, ids=ascii)
def test_a_run_record_whose_id_is_a_path_stops_every_command_that_reads_it(tmp_path: Path, run_id):
    project = build_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "stolen.jsonl").write_bytes(_events())
    _sidecar(project, run_id)
    with pytest.raises(BundleError, match="refusing the run id"):
        plan_bundle(project_root=project)  # what `steppy run --upload` and `bundle upload` use
    with pytest.raises(BundleError, match="refusing the run id"):
        create_bundle(project_root=project, fmt="gz")
    with pytest.raises(Exception, match="refusing the run id"):
        build_report(project_root=project, site_dir=tmp_path / "site")
    assert not [p for p in tmp_path.rglob("*.tar.gz")]


def test_a_run_id_given_by_hand_is_held_to_the_same_rule(tmp_path: Path):
    project = build_project(tmp_path)
    with pytest.raises(BundleError, match="--run-id"):
        create_bundle(project_root=project, fmt="gz", run_id="../../evil")


@pytest.mark.parametrize("which", ["events", "junit"])
def test_a_record_directory_that_links_out_is_refused(tmp_path: Path, which):
    project = build_project(tmp_path)
    run_id = "20991231T000000Z-abcdef"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "stolen.jsonl").write_bytes(_events())
    _sidecar(project, run_id)
    _link(project / "output" / which / run_id, outside)
    with pytest.raises(BundleError, match="refusing to read"):
        plan_bundle(project_root=project)


def test_a_linked_record_file_is_skipped_and_said(tmp_path: Path):
    from .test_bundle import RUN_ID

    project = build_project(tmp_path)
    stolen = tmp_path / "stolen.jsonl"
    stolen.write_bytes(_events())
    try:
        (project / "output" / "events" / RUN_ID / "z.jsonl").symlink_to(stolen)
    except (OSError, NotImplementedError):
        pytest.skip("this system lets a test make no link")
    plan = plan_bundle(project_root=project, run_id=RUN_ID)
    assert NODEID not in {t.nodeid for t in plan.manifest.tests}
    assert any(w == f"skipped symlink events/{RUN_ID}/z.jsonl" for w in plan.manifest.warnings)


@pytest.mark.skipif(os.name != "nt", reason="a junction is a Windows link")
def test_a_junction_in_the_output_tree_is_skipped_and_said(tmp_path: Path):
    """``rglob`` follows a junction where it does not follow a symlink."""
    import _winapi

    project = build_project(tmp_path)
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "deep" / "secret.txt").write_bytes(SECRET)
    _winapi.CreateJunction(str(outside), str(project / "output" / "linked"))
    plan = plan_bundle(project_root=project)
    assert not [f.path for f in plan.manifest.files if "linked" in f.path]
    assert "skipped junction linked" in plan.manifest.warnings


# ── bundle upload <archive>, inspect, verify: nothing is joined ─────────────


def test_an_uploads_report_says_what_it_refused(lair: Lair):
    archive = lair.archive([_shot("../outside/secret.png")], {"../outside/secret.png": b"x"})
    document = build_document(open_source(archive, project_root=lair.project), project_root=lair.project)
    assert sum("may leave the bundle" in w for w in document.warnings) == 1


def test_inspect_and_verify_read_the_archive_alone(lair: Lair, monkeypatch):
    """They name members and hash them: no manifest path meets the file system."""
    archive = lair.archive([_shot("../outside/secret.png")], {"../outside/secret.png": b"x"})
    (lair.outside / "secret.png").unlink()
    assert inspect_bundle(archive).files
    _manifest_read, problems = verify_bundle(archive)
    assert all("secret.png" in p or "events" in p for p in problems)
    assert list(lair.outside.iterdir()) == []


# ── destinations ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["con", "NUL", "aux.x", "com1", "lpt9"])
def test_a_destination_is_never_a_windows_device(name):
    from steppy.report.assets import sanitise

    assert sanitise(name) == f"_{name}"
    assert sanitise("console") == "console"
