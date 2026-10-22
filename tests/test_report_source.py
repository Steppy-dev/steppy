"""Three ways in, one result — and the read-side path-traversal defence.

The report is handed a run as a live ``output/`` tree, as a downloaded archive, or
as a directory someone already unpacked. What is pinned here is that all three
produce the same manifest and the same bytes, because every other module in
``steppy.report`` is written assuming they do.

The traversal test is the one that matters most. Streaming members out of a tar
member-by-member skips Python's extraction filters, and an arcname inside a
downloaded bundle is attacker-controlled — so destinations must come from the
caller and nowhere else.
"""

import io
import json
import tarfile
from pathlib import Path

import pytest

from steppy.bundle import create_bundle
from steppy.bundle.archive import read_members, zstandard_available
from steppy.report.source import KIND_ARCHIVE, KIND_DIR, KIND_TREE, ReportSourceError, open_source

from .test_bundle import build_project


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return build_project(tmp_path)


def _unpack(archive: Path, dest: Path) -> Path:
    with tarfile.open(archive, mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    return dest


# ── the three modes agree ───────────────────────────────────────────────────


def test_live_tree_is_the_default_input(project: Path):
    source = open_source(None, project_root=project)
    assert source.kind == KIND_TREE
    assert source.is_live_tree is True
    assert source.manifest.steppy_bundle == 1


def test_all_three_modes_yield_the_same_manifest_and_bytes(project: Path, tmp_path: Path):
    """The reporter is written once against whichever of the three it was given."""
    archive = create_bundle(project_root=project, fmt="gz").path
    unpacked = _unpack(archive, tmp_path / "unpacked")

    sources = [
        open_source(None, project_root=project),
        open_source(archive, project_root=project),
        open_source(unpacked, project_root=project),
    ]
    assert {s.kind for s in sources} == {KIND_TREE, KIND_ARCHIVE, KIND_DIR}

    fingerprints = [{f.path: f.sha256 for f in s.manifest.files} for s in sources]
    assert fingerprints[0] == fingerprints[1] == fingerprints[2]

    wanted = {"junit.xml"}
    fetched = []
    for i, source in enumerate(sources):
        texts, warnings = source.fetch(texts=wanted)
        assert warnings == [], (source.kind, warnings)
        fetched.append(texts)
        # And the same bytes land on disk when a destination is given.
        target = tmp_path / f"out{i}" / "junit.xml"
        source.fetch(into={"junit.xml": target})
        assert target.read_bytes() == texts["junit.xml"]
    assert fetched[0] == fetched[1] == fetched[2]


def test_a_binary_member_copies_to_the_caller_chosen_path(project: Path, tmp_path: Path):
    archive = create_bundle(project_root=project, fmt="gz").path
    shot = next(f.path for f in open_source(archive, project_root=project).manifest.files if f.kind == "screenshot")

    for label, arg in (("tree", None), ("archive", archive)):
        source = open_source(arg, project_root=project)
        target = tmp_path / label / "capture.png"
        _, warnings = source.fetch(into={shot: target})
        assert warnings == [], label
        assert target.is_file(), label


def test_only_requested_members_are_written(project: Path, tmp_path: Path):
    """The whole point of declaring the wanted set up front: a report that copies
    only failure captures must not pay for the other four hundred."""
    archive = create_bundle(project_root=project, fmt="gz").path
    source = open_source(archive, project_root=project)
    dest = tmp_path / "assets"
    source.fetch(into={"junit.xml": dest / "junit.xml"})
    assert [p.name for p in dest.iterdir()] == ["junit.xml"]


@pytest.mark.skipif(not zstandard_available(), reason="zstandard not installed")
def test_the_zstd_stream_works_despite_being_non_seekable(project: Path):
    """No ``getmember``, no rewind — the reason members are requested up front."""
    archive = create_bundle(project_root=project, fmt="zst").path
    source = open_source(archive, project_root=project)
    texts, warnings = source.fetch(texts={"junit.xml"})
    assert warnings == [] and texts["junit.xml"]


# ── honest degradation ──────────────────────────────────────────────────────


def test_a_member_absent_from_the_archive_is_a_warning_not_a_crash(project: Path, tmp_path: Path):
    source = open_source(create_bundle(project_root=project, fmt="gz").path, project_root=project)
    texts, warnings = source.fetch(into={"output/nope.png": tmp_path / "nope.png"})
    assert texts == {}
    assert any("absent from the archive" in w for w in warnings)


def test_a_member_missing_on_disk_is_a_warning_not_a_crash(project: Path, tmp_path: Path):
    source = open_source(None, project_root=project)
    _, warnings = source.fetch(into={"output/screenshots/gone.png": tmp_path / "gone.png"})
    assert any("missing on disk" in w for w in warnings)


# ── input validation ────────────────────────────────────────────────────────


def test_a_directory_without_a_manifest_is_rejected_with_a_hint(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ReportSourceError, match="contains no manifest.json"):
        open_source(tmp_path / "empty", project_root=tmp_path)


def test_a_file_that_is_not_a_bundle_is_rejected(tmp_path: Path):
    stray = tmp_path / "results.zip"
    stray.write_bytes(b"PK")
    with pytest.raises(ReportSourceError, match="neither a .tar.zst"):
        open_source(stray, project_root=tmp_path)


def test_a_missing_path_is_rejected(tmp_path: Path):
    with pytest.raises(ReportSourceError, match="no such bundle"):
        open_source(tmp_path / "ghost.tar.gz", project_root=tmp_path)


def test_a_project_with_no_output_tree_is_rejected_with_the_next_command(tmp_path: Path):
    (tmp_path / "config.yaml").write_text("apps: {}\n", encoding="utf-8")
    with pytest.raises(ReportSourceError, match="steppy run"):
        open_source(None, project_root=tmp_path)


def test_a_corrupt_manifest_in_a_directory_is_rejected(tmp_path: Path):
    root = tmp_path / "unpacked"
    root.mkdir()
    (root / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ReportSourceError, match="could not read"):
        open_source(root, project_root=tmp_path)


def test_a_manifest_from_an_unsupported_contract_version_is_rejected(tmp_path: Path):
    root = tmp_path / "unpacked"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"steppy_bundle": 99, "run": {}}), encoding="utf-8")
    with pytest.raises(ReportSourceError):
        open_source(root, project_root=tmp_path)


# ── the traversal defence ───────────────────────────────────────────────────


def _hostile_archive(target: Path, members: list[tuple[str, bytes]]) -> Path:
    with tarfile.open(target, mode="w:gz") as tar:
        for name, payload in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return target


def test_a_manifest_path_is_never_used_as_a_destination(project: Path, tmp_path: Path):
    """``read_members`` writes only where the caller said. An arcname escaping the
    target would be a write outside it, which is why destinations are computed
    from validated components instead of taken from the manifest."""
    archive = _hostile_archive(tmp_path / "evil.tar.gz", [("../../evil.png", b"pwned")])
    dest = tmp_path / "site" / "safe.png"
    fetched, warnings = read_members(archive, into={"../../evil.png": dest})
    assert fetched == {} and warnings == []
    assert dest.read_bytes() == b"pwned"  # landed where WE chose
    assert not (tmp_path.parent / "evil.png").exists()
    assert not (tmp_path / "evil.png").exists()


def test_a_non_regular_member_is_skipped_with_a_warning(tmp_path: Path):
    """A hostile archive can carry symlinks and hardlinks; ``extractfile`` returns
    None for some of them, and following one would write outside the site."""
    archive = tmp_path / "links.tar.gz"
    with tarfile.open(archive, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../etc/passwd"
        tar.addfile(info)
    fetched, warnings = read_members(archive, into={"link": tmp_path / "out" / "link"})
    assert fetched == {}
    assert any("not a regular file" in w for w in warnings)
    assert not (tmp_path / "out" / "link").exists()


def test_read_members_with_nothing_requested_reads_nothing(project: Path):
    archive = create_bundle(project_root=project, fmt="gz").path
    assert read_members(archive) == ({}, [])
