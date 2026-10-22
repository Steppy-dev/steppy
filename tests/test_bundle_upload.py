"""``steppy bundle upload`` — the client half of the upload protocol.

Driven against a fake control plane wired in through ``httpx.MockTransport``,
which is a real transport: the client's own header merging, ``Content-Length``,
retries and the two-client split (presigned PUTs must carry no
``Authorization``) are all exercised. Stubbing ``CloudClient`` instead would
verify only that the stub was called.

The fake speaks the same routes the plane does — ``docs/contracts/job-api-v1.md``
over there, ``tests/pg/test_local_run_lifecycle.py`` for the server-side proof
that they behave this way.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from tests.test_bundle import build_project

from steppy.bundle import create_bundle, plan_bundle
from steppy.cloud import ArchiveSource, CloudAuthError, CloudClient, CloudError, TreeSource, upload_bundle
from steppy.cloud.client import Endpoint, resolve_endpoint

BLOB_HOST = "https://blobs.test"


class FakePlane:
    """Enough control plane to run the protocol against.

    Keeps a per-org blob set, so the dedup assertion ("a second upload of the
    same run sends nothing") is a property of the fake's *storage* rather than
    of a canned response.
    """

    def __init__(self, *, claim_conflicts: int = 0, expire_puts: int = 0) -> None:
        self.blobs: dict[str, bytes] = {}
        self.runs: dict[str, dict] = {}
        self.by_key: dict[str, str] = {}
        self.offers: dict[str, dict] = {}
        self.calls: list[str] = []
        self.manifests: list[dict] = []
        self.completed: list[dict] = []
        self.auth: list[str | None] = []
        self._claim_conflicts = claim_conflicts
        self._expire_puts = expire_puts

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        if str(request.url).startswith(BLOB_HOST):
            return self._put_blob(request)

        self.calls.append(f"{method} {path}")
        body = json.loads(request.content) if request.content else {}

        if method == "POST" and path == "/v1/runs":
            return self._create_run(request, body)
        if method == "GET" and path.startswith("/v1/runs/"):
            return httpx.Response(200, json=self.runs[path.split("/")[3]])
        if method == "POST" and path.endswith("/claim"):
            return self._claim(path.split("/")[3])
        if method == "POST" and path.endswith("/bundle"):
            return self._offer(path.split("/")[3], body, request)
        if method == "POST" and path.endswith("/bundle/finalize"):
            return self._finalize(path.split("/")[3])
        if method == "POST" and path.endswith("/complete"):
            self.completed.append(body)
            return httpx.Response(200, json={"task_id": "T1", "run_status": "ingesting"})
        return httpx.Response(404, json={"detail": f"no route {method} {path}"})

    def _create_run(self, request: httpx.Request, body: dict) -> httpx.Response:
        key = request.headers.get("Idempotency-Key")
        if key and key in self.by_key:
            return httpx.Response(200, json=self.runs[self.by_key[key]])
        run_id = f"R{len(self.runs) + 1}"
        self.runs[run_id] = {
            "id": run_id,
            "number": len(self.runs) + 1,
            "status": "queued",
            "executor": body.get("executor"),
            "spec": body.get("spec", {}),
            "scenarios": body.get("scenarios", []),
        }
        if key:
            self.by_key[key] = run_id
        return httpx.Response(201, json=self.runs[run_id])

    def _claim(self, run_id: str) -> httpx.Response:
        if self._claim_conflicts:
            self._claim_conflicts -= 1
            return httpx.Response(409, json={"detail": "no claimable whole-run task"})
        self.runs[run_id]["status"] = "running"
        return httpx.Response(
            200,
            json={
                "kind": "run",
                "task_id": "T1",
                "run_id": run_id,
                "run_number": self.runs[run_id]["number"],
                "project_id": "P1",
                "scenario_slug": None,
                "attempt": 1,
                "git": {"sha": None, "branch": None, "pr_number": None, "fetch_token": None},
                "spec": {},
                "claim_token": "stp_run_faketoken",
                "lease_expires_at": "2026-08-08T12:01:00Z",
                "heartbeat_interval_s": 20,
                "auth_handoff": None,
            },
        )

    def _offer(self, run_id: str, manifest: dict, request: httpx.Request) -> httpx.Response:
        self.manifests.append(manifest)
        self.auth.append(request.headers.get("Authorization"))
        files = {f["sha256"]: f for f in manifest["files"]}
        missing = [s for s in files if s not in self.blobs]
        self.offers[run_id] = {"expected": set(missing)}
        return httpx.Response(
            200,
            json={
                "upload_id": "U1",
                "uploads": [{"sha256": s, "size": files[s]["size"], "url": f"{BLOB_HOST}/{s}"} for s in missing],
                "already_held": len(files) - len(missing),
                "total_files": len(manifest["files"]),
                "expires_at": "2026-08-08T13:00:00Z",
            },
        )

    def _finalize(self, run_id: str) -> httpx.Response:
        expected = self.offers[run_id]["expected"]
        absent = [s for s in expected if s not in self.blobs]
        if absent:
            return httpx.Response(409, json={"detail": f"{len(absent)} blob(s) were never uploaded"})
        self.runs[run_id]["status"] = "ingesting"
        return httpx.Response(
            200,
            json={"upload_id": "U1", "verified": len(expected), "already_finalized": False, "run_status": "ingesting"},
        )

    def _put_blob(self, request: httpx.Request) -> httpx.Response:
        sha = request.url.path.lstrip("/")
        # A presigned URL carries its authorisation in the query string. A store
        # that also receives an Authorization header rejects the pair, so this
        # is the assertion that keeps the two-client split honest.
        assert "Authorization" not in request.headers, "a presigned PUT must not carry a bearer token"
        if self._expire_puts:
            self._expire_puts -= 1
            return httpx.Response(403, text="<Error><Code>AccessDenied</Code>Request has expired</Error>")
        data = request.read()
        assert request.headers["Content-Length"] == str(len(data))
        assert hashlib.sha256(data).hexdigest() == sha, "uploaded bytes do not match the digest asked for"
        self.blobs[sha] = data
        return httpx.Response(200)


def client_for(plane: FakePlane) -> CloudClient:
    return CloudClient(Endpoint(url="https://plane.test", token="stp_pat_x"), transport=plane.transport, retries=0)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return build_project(tmp_path)


def tree_source(project: Path) -> TreeSource:
    return TreeSource(plan_bundle(project_root=project))


# --- endpoint resolution ----------------------------------------------------


def test_url_precedence_is_flag_then_env_then_config(monkeypatch) -> None:
    monkeypatch.setenv("STEPPY_CLOUD_TOKEN", "t")
    monkeypatch.setenv("STEPPY_CLOUD_URL", "https://from-env")
    assert resolve_endpoint(config_url="https://from-config").url == "https://from-env"
    assert resolve_endpoint("https://from-flag", config_url="https://from-config").url == "https://from-flag"
    monkeypatch.delenv("STEPPY_CLOUD_URL")
    assert resolve_endpoint(config_url="https://from-config").url == "https://from-config"


def test_a_pasted_v1_url_is_accepted(monkeypatch) -> None:
    """`/v1` is what every error message and doc shows, so it is what gets
    pasted. Appending a second one would 404 in a way that reads like an outage."""
    monkeypatch.setenv("STEPPY_CLOUD_TOKEN", "t")
    assert resolve_endpoint("https://plane.test/v1/").api == "https://plane.test/v1"


def test_a_missing_token_names_both_places_it_can_come_from(monkeypatch) -> None:
    monkeypatch.delenv("STEPPY_CLOUD_TOKEN", raising=False)
    with pytest.raises(CloudError) as e:
        resolve_endpoint("https://plane.test")
    assert "STEPPY_CLOUD_TOKEN" in str(e.value) and "--token" in str(e.value)


def test_the_token_never_appears_in_a_repr() -> None:
    """`--verbose` prints tracebacks, and a dataclass repr would put a live
    credential in one."""
    assert "sekrit" not in repr(Endpoint(url="https://plane.test", token="stp_pat_sekrit"))


# --- the protocol -----------------------------------------------------------


def test_upload_sends_every_blob_once_and_then_finalizes(project: Path) -> None:
    plane = FakePlane()
    source = tree_source(project)
    with client_for(plane) as client:
        outcome = upload_bundle(client, source, settle_timeout=0)

    digests = {f.sha256 for f in source.manifest.files}
    assert set(plane.blobs) == digests
    assert outcome.uploaded == len(digests)
    assert outcome.uploaded_bytes == sum(len(b) for b in plane.blobs.values())
    assert outcome.total_files == len(source.manifest.files)
    assert outcome.created_run is True


def test_finalize_is_sent_before_complete(project: Path) -> None:
    """The ordering `upload_bundle`'s docstring argues for, asserted.

    Completing first lets the plane land a verdict with no bundle pending, and
    its close statement then refuses to move a terminal run to `ingesting`.
    """
    plane = FakePlane()
    with client_for(plane) as client:
        upload_bundle(client, tree_source(project), settle_timeout=0)

    order = [c for c in plane.calls if c.endswith("/bundle/finalize") or c.endswith("/complete")]
    assert order == ["POST /v1/runs/R1/bundle/finalize", "POST /v1/tasks/T1/complete"]


def test_the_second_upload_of_an_unchanged_run_sends_nothing(project: Path) -> None:
    """The economic argument for the whole protocol, as an assertion."""
    plane = FakePlane()
    with client_for(plane) as client:
        upload_bundle(client, tree_source(project), settle_timeout=0)
        again = upload_bundle(client, tree_source(project), settle_timeout=0)

    assert again.uploaded == 0
    assert again.uploaded_bytes == 0
    assert again.skipped_bytes == again.payload_bytes
    # In digests, not files — the server counts `already_held` per blob while
    # `total_files` counts manifest entries, and this fixture has screenshots
    # that are byte-identical across locales.
    assert again.already_held == again.total_blobs < again.total_files


def test_the_bundle_run_id_is_the_idempotency_key(project: Path) -> None:
    """A retried upload must attach to the run the first attempt made, not burn
    a second run number for one set of results."""
    plane = FakePlane()
    with client_for(plane) as client:
        first = upload_bundle(client, tree_source(project), settle_timeout=0)
        second = upload_bundle(client, tree_source(project), settle_timeout=0)

    assert first.run_id == second.run_id
    assert len(plane.runs) == 1


def test_an_expired_presigned_url_triggers_exactly_one_re_offer(project: Path) -> None:
    """A URL that expires mid-upload is ordinary on a slow link. The offer is
    keyed on (run, task) and reports what is already held, so re-offering asks
    for the remainder and nothing already sent is re-sent."""
    plane = FakePlane(expire_puts=1)
    source = tree_source(project)
    with client_for(plane) as client:
        outcome = upload_bundle(client, source, concurrency=1, settle_timeout=0)

    assert plane.calls.count("POST /v1/runs/R1/bundle") == 2
    assert set(plane.blobs) == {f.sha256 for f in source.manifest.files}
    # Counted as it happened, not inferred: the blobs that landed before the
    # expiry are not re-sent and must not be double-counted either.
    assert outcome.uploaded == len(plane.blobs)


def test_a_second_expiry_is_not_retried_through(project: Path) -> None:
    plane = FakePlane(expire_puts=99)
    with client_for(plane) as client, pytest.raises(CloudError) as e:
        upload_bundle(client, tree_source(project), concurrency=1, settle_timeout=0)
    assert e.value.status == 403
    assert plane.calls.count("POST /v1/runs/R1/bundle") == 2


def test_the_claim_is_retried_while_the_run_is_still_queued(project: Path) -> None:
    """Dispatch is a background job: the run exists the moment POST /runs
    answers, but its task is written a moment later by a worker."""
    plane = FakePlane(claim_conflicts=2)
    with client_for(plane) as client:
        upload_bundle(client, tree_source(project), claim_timeout=10, settle_timeout=0)

    assert plane.calls.count("POST /v1/runs/R1/claim") == 3


def test_a_claim_conflict_on_a_running_run_is_not_retried(project: Path) -> None:
    """A 409 once the run has left `queued` means somebody else owns it, and
    retrying is how two uploads race each other forever."""
    plane = FakePlane(claim_conflicts=99)
    plane.runs["R1"] = {"id": "R1", "number": 1, "status": "running"}
    plane.by_key["20260806T100000Z-aaaaaa"] = "R1"

    with client_for(plane) as client, pytest.raises(CloudError) as e:
        upload_bundle(client, tree_source(project), claim_timeout=10, settle_timeout=0)
    assert "another executor owns it" in str(e.value)
    assert plane.calls.count("POST /v1/runs/R1/claim") == 1


def test_the_upload_authenticates_with_the_claim_token_not_the_project_token(project: Path) -> None:
    """The narrowest credential that can do the job, and the one that files the
    upload under the task — which is what an executor pushing its own partial
    bundle will need."""
    plane = FakePlane()
    with client_for(plane) as client:
        upload_bundle(client, tree_source(project), settle_timeout=0)
    assert plane.auth == ["Bearer stp_run_faketoken"]


def test_an_unauthorised_call_is_never_retried() -> None:
    def deny(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(401, json={"detail": "invalid or expired credentials"})

    calls: list[str] = []
    client = CloudClient(Endpoint(url="https://plane.test", token="bad"), transport=httpx.MockTransport(deny))
    with client, pytest.raises(CloudAuthError):
        client.get("/runs/R1")
    assert len(calls) == 1, "401 means stop, not back off"


# --- the verdict ------------------------------------------------------------


def test_the_exit_code_is_derived_from_the_manifest_outcomes(project: Path) -> None:
    plane = FakePlane()
    source = tree_source(project)
    assert any(t.outcome == "failed" for t in source.manifest.tests), "the fixture should carry a failure"

    with client_for(plane) as client:
        outcome = upload_bundle(client, source, settle_timeout=0)

    assert outcome.exit_code == 1
    assert plane.completed == [{"exit_code": 1, "duration_ms": None}]


def test_an_explicit_exit_code_wins(project: Path) -> None:
    """`steppy ci run` will know the real one; upload after the fact never does."""
    plane = FakePlane()
    with client_for(plane) as client:
        upload_bundle(client, tree_source(project), exit_code=0, settle_timeout=0)
    assert plane.completed == [{"exit_code": 0, "duration_ms": None}]


# --- the two sources agree --------------------------------------------------


def test_uploading_an_archive_sends_the_same_bytes_as_the_live_tree(project: Path, tmp_path: Path) -> None:
    """The two source modes must be interchangeable, or a CI job that archives
    in one step and uploads in another ships something different from a laptop."""
    archive = create_bundle(project_root=project, target=tmp_path / "b.tar.gz", fmt="gz").path

    from_tree = FakePlane()
    with client_for(from_tree) as client:
        upload_bundle(client, tree_source(project), settle_timeout=0)

    from_archive = FakePlane()
    with client_for(from_archive) as client:
        upload_bundle(client, ArchiveSource(archive), settle_timeout=0)

    assert from_archive.blobs == from_tree.blobs
    assert from_archive.manifests[0]["files"] == from_tree.manifests[0]["files"]


def test_the_offer_body_is_the_manifest_itself(project: Path) -> None:
    """Not a wrapper: it is what `steppy bundle create` already writes to disk,
    and the server parses it with the same contract module that produced it."""
    plane = FakePlane()
    source = tree_source(project)
    with client_for(plane) as client:
        upload_bundle(client, source, settle_timeout=0)

    assert plane.manifests[0] == source.manifest.model_dump(mode="json")
    assert plane.manifests[0]["steppy_bundle"] == 1
