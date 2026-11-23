"""``steppy bundle upload`` and the upload protocol (:mod:`steppy.contracts.upload`).

Driven against :class:`tests._fake_cloud.FakeCloud` through ``httpx.MockTransport``:
create the run with the manifest, PUT what the server wants and lacks, finalize.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests._fake_cloud import GOOD_KEY, FakeCloud
from tests.test_bundle import build_project
from typer.testing import CliRunner

from steppy.bundle import create_bundle, plan_bundle
from steppy.cloud import ArchiveSource, CloudError, TreeSource, attach_report, upload_run
from steppy.contracts.report import parse_report
from steppy.contracts.upload import REPORT_KIND, REPORT_PATH, report_file

_ENV = ("STEPPY_API_KEY", "STEPPY_URL", "STEPPY_CLOUD_TOKEN", "STEPPY_CLOUD_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return build_project(tmp_path)


def reported(project: Path):
    source, _model = attach_report(TreeSource(plan_bundle(project_root=project)), project_root=project)
    return source


def upload(cloud: FakeCloud, source, **kwargs):
    with cloud.client() as client:
        return upload_run(client, source, **kwargs)


# --- report.json --------------------------------------------------------------


def test_report_json_is_a_file_of_the_manifest(project: Path) -> None:
    source = reported(project)
    entry = report_file(source.manifest)
    assert entry is not None and entry.path == REPORT_PATH and entry.kind == REPORT_KIND
    assert entry.size == len(source.report)
    # Sorted with the others, like every manifest the bundler writes.
    assert [f.path for f in source.manifest.files] == sorted(f.path for f in source.manifest.files)


def test_report_json_is_the_report_model_with_captures_by_manifest_path(project: Path) -> None:
    """The contract: a capture reference is the file's manifest path, which a
    server resolves through `files[].sha256` — there is no site to link into."""
    source = reported(project)
    model = parse_report(json.loads(source.report))
    paths = {f.path for f in source.manifest.files}

    assert model.run.run_id == source.manifest.run.run_id
    assert model.totals.verdict == "failed"
    assert model.capabilities.screenshots == "all"
    shots = [s for sc in model.scenarios for v in sc.variants for st in v.steps for s in st.screenshots]
    failure_shots = [v.failure_screenshot for sc in model.scenarios for v in sc.variants if v.failure_screenshot]
    assert shots and failure_shots
    assert set(shots + failure_shots) <= paths


def test_the_report_is_reproducible_for_an_unchanged_run(project: Path) -> None:
    """Same run, same bytes: so a second upload of it sends no new report."""
    assert reported(project).report == reported(project).report


# --- the protocol -------------------------------------------------------------


def test_create_sends_the_manifest_with_the_run_id_as_idempotency_key(project: Path) -> None:
    cloud = FakeCloud()
    source = reported(project)
    upload(cloud, source)

    request = cloud.creates[0]
    assert request.headers["Idempotency-Key"] == source.manifest.run.run_id
    assert request.headers["Authorization"] == f"Bearer {GOOD_KEY}"
    body = json.loads(request.content)
    assert body["manifest"] == source.manifest.model_dump(mode="json")
    assert body["manifest"]["steppy_bundle"] == 1


def test_the_calls_are_create_put_finalize_and_nothing_else(project: Path) -> None:
    """No claim, no task, no heartbeat, no status polling."""
    cloud = FakeCloud()
    upload(cloud, reported(project))
    assert cloud.calls == ["POST /api/v1/runs", "POST /api/v1/runs/R1/finalize"]


def test_every_wanted_file_is_sent_once_then_the_run_is_finalized(project: Path) -> None:
    cloud = FakeCloud()
    source = reported(project)
    outcome = upload(cloud, source)

    digests = {f.sha256 for f in source.manifest.files}
    assert set(cloud.blobs) == digests
    assert sorted(cloud.puts) == sorted(digests), "a digest shared by two paths is one upload"
    assert outcome.uploaded == outcome.requested == outcome.total_blobs == len(digests)
    assert outcome.total_files == len(source.manifest.files) > outcome.total_blobs
    assert outcome.uploaded_bytes == sum(len(b) for b in cloud.blobs.values())
    assert cloud.finalized == {"R1": 1}
    assert outcome.url == "https://cloud.test/r/R1"
    assert outcome.status == "failed"
    assert outcome.number == 1


def test_only_the_files_the_server_lacks_are_uploaded(project: Path) -> None:
    cloud = FakeCloud()
    source = reported(project)
    screenshots = {f.sha256: b"held" for f in source.manifest.files if f.kind == "screenshot"}
    cloud.blobs.update(screenshots)

    outcome = upload(cloud, source)

    assert not set(cloud.puts) & set(screenshots)
    assert set(cloud.puts) == {f.sha256 for f in source.manifest.files} - set(screenshots)
    assert outcome.uploaded == outcome.requested == len(cloud.puts)


def test_only_the_files_the_server_wants_are_uploaded(project: Path) -> None:
    """The server keeps what its policy says (by default the screenshots of
    failed variants); the rest is never sent and the run still finalizes."""
    cloud = FakeCloud(wants=lambda f: f["kind"] not in ("video", "trace", "screenshot"))
    source = reported(project)
    upload(cloud, source)

    sent_kinds = {f.kind for f in source.manifest.files if f.sha256 in cloud.puts}
    assert sent_kinds and not sent_kinds & {"video", "trace", "screenshot"}
    assert REPORT_KIND in sent_kinds
    assert cloud.finalized == {"R1": 1}


def test_creating_the_same_run_again_returns_it_and_sends_nothing(project: Path) -> None:
    """Re-upload: the same Idempotency-Key gets the same run back with nothing
    missing, and the second finalize changes nothing."""
    cloud = FakeCloud()
    first = upload(cloud, reported(project))
    again = upload(cloud, reported(project))

    assert again.run_id == first.run_id == "R1"
    assert len(cloud.runs) == 1
    assert again.requested == again.uploaded == again.uploaded_bytes == 0
    assert cloud.finalized == {"R1": 2}
    assert again.url == first.url


def test_a_different_run_is_a_different_run(project: Path, tmp_path: Path) -> None:
    cloud = FakeCloud()
    upload(cloud, reported(project))
    source = TreeSource(plan_bundle(project_root=project, run_id="20260807T100000Z-cccccc"))
    second = upload(cloud, attach_report(source, project_root=project)[0])
    assert second.run_id == "R2"
    # The captures are the same bytes: only what changed travels.
    assert second.uploaded < second.total_blobs


def test_an_expired_upload_url_is_replaced_by_creating_the_run_again(project: Path) -> None:
    cloud = FakeCloud(expire_puts=1)
    source = reported(project)
    outcome = upload(cloud, source, concurrency=1)

    assert cloud.calls.count("POST /api/v1/runs") == 2
    assert set(cloud.blobs) == {f.sha256 for f in source.manifest.files}
    # Counted as it happened, not inferred: nothing sent twice, nothing counted twice.
    assert outcome.uploaded == len(cloud.puts) == len(set(cloud.puts))


def test_a_second_expiry_is_not_retried_through(project: Path) -> None:
    cloud = FakeCloud(expire_puts=99)
    with pytest.raises(CloudError) as e:
        upload(cloud, reported(project), concurrency=1)
    assert e.value.status == 403
    assert cloud.calls.count("POST /api/v1/runs") == 2
    assert not cloud.finalized


def test_the_put_carries_the_headers_its_url_was_signed_with(project: Path) -> None:
    cloud = FakeCloud(put_headers={"Content-Type": "application/octet-stream"})
    upload(cloud, reported(project))
    assert cloud.finalized == {"R1": 1}


def test_a_digest_the_run_does_not_describe_is_refused(project: Path) -> None:
    cloud = FakeCloud()
    source = reported(project)

    def lying(request):
        response = cloud._handle(request)
        if request.url.path == "/api/v1/runs":
            body = json.loads(response.content)
            body["uploads"].append({"sha256": "f" * 64, "url": "https://blobs.test/" + "f" * 64})
            return type(response)(response.status_code, json=body)
        return response

    import httpx

    from steppy.cloud import CloudClient, Endpoint

    client = CloudClient(Endpoint(api="https://cloud.test/api/v1", key=GOOD_KEY), transport=httpx.MockTransport(lying))
    with client, pytest.raises(CloudError, match="does not describe"):
        upload_run(client, source)


def test_uploading_an_archive_sends_the_same_bytes_as_the_live_tree(project: Path, tmp_path: Path) -> None:
    """The two source modes must be interchangeable, or a CI job that archives
    in one step and uploads in another ships something different from a laptop."""
    archive = create_bundle(project_root=project, target=tmp_path / "b.tar.gz", fmt="gz").path

    from_tree = FakeCloud()
    upload(from_tree, reported(project))
    from_archive = FakeCloud()
    upload(from_archive, attach_report(ArchiveSource(archive), project_root=project)[0])

    tree_files = json.loads(from_tree.creates[0].content)["manifest"]["files"]
    archive_files = json.loads(from_archive.creates[0].content)["manifest"]["files"]
    # report.json names its source (tree vs archive), so its bytes differ; every
    # other file is the same bytes at the same path.
    assert [f for f in archive_files if f["path"] != REPORT_PATH] == [f for f in tree_files if f["path"] != REPORT_PATH]
    report = {f["sha256"] for f in tree_files + archive_files if f["path"] == REPORT_PATH}
    assert {k: v for k, v in from_archive.blobs.items() if k not in report} == {
        k: v for k, v in from_tree.blobs.items() if k not in report
    }


# --- the command --------------------------------------------------------------


def _invoke(project: Path, cloud: FakeCloud, monkeypatch, *args: str):
    from steppy.cli import app

    monkeypatch.setattr("steppy.cloud.connect", cloud.connect)
    return CliRunner().invoke(app, ["bundle", "upload", "--project-root", str(project), *args])


def test_the_command_uploads_and_prints_the_run_url(project: Path, monkeypatch) -> None:
    cloud = FakeCloud()
    result = _invoke(project, cloud, monkeypatch, "--url", "https://cloud.test", "--api-key", GOOD_KEY)
    assert result.exit_code == 0, result.output
    assert "https://cloud.test/r/R1" in result.output
    assert cloud.finalized == {"R1": 1}


def test_the_command_reads_the_environment(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("STEPPY_URL", "https://cloud.test")
    monkeypatch.setenv("STEPPY_API_KEY", GOOD_KEY)
    cloud = FakeCloud()
    result = _invoke(project, cloud, monkeypatch, "--json")
    assert result.exit_code == 0, result.output
    outcome = json.loads(result.stdout)
    assert outcome["run_id"] == "R1" and outcome["url"] == "https://cloud.test/r/R1"


def test_the_old_token_flag_still_works(project: Path, monkeypatch) -> None:
    cloud = FakeCloud()
    result = _invoke(project, cloud, monkeypatch, "--url", "https://cloud.test", "--token", GOOD_KEY)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "cloud,args,expected",
    [
        (FakeCloud(), ["--url", "https://cloud.test", "--api-key", "stp_wrong"], "HTTP 401"),
        (FakeCloud(unreachable=True), ["--url", "https://cloud.test", "--api-key", GOOD_KEY], "could not reach"),
        (FakeCloud(fail_with=503), ["--url", "https://cloud.test", "--api-key", GOOD_KEY], "HTTP 503"),
        (FakeCloud(), ["--url", "https://cloud.test"], "no API key"),
        (FakeCloud(), ["--api-key", GOOD_KEY], "no Steppy Cloud address"),
    ],
    ids=["refused", "unreachable", "server-error", "no-key", "no-url"],
)
def test_an_explicit_upload_that_fails_exits_non_zero(project: Path, monkeypatch, cloud, args, expected) -> None:
    result = _invoke(project, cloud, monkeypatch, *args)
    assert result.exit_code == 1, result.output
    assert expected in result.output
    assert not cloud.finalized


def test_a_422_prints_the_servers_detail(project: Path, monkeypatch) -> None:
    """A report version the server does not accept: its detail says what to do."""
    cloud = FakeCloud(fail_with=422)
    result = _invoke(project, cloud, monkeypatch, "--url", "https://cloud.test", "--api-key", GOOD_KEY)
    assert result.exit_code == 1
    assert "the fake answers 422" in result.output


def test_dry_run_contacts_nothing(project: Path, monkeypatch) -> None:
    cloud = FakeCloud()
    result = _invoke(project, cloud, monkeypatch, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "report.json included" in " ".join(result.output.split())
    assert cloud.calls == [] and cloud.puts == []
