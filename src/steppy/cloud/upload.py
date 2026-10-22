"""``steppy bundle upload`` — push one run's results to a control plane.

The protocol is content-addressed and has three steps::

    ① POST /v1/runs/{id}/bundle           manifest  → PUT URLs for missing digests
    ② PUT  <presigned url>                bytes     → the object store, directly
    ③ POST /v1/runs/{id}/bundle/finalize  verify    → ingestion is queued

Step ① is the whole point. With locales x layouts x device scale factors an
unchanged screenshot dominates a bundle, so the server answers with URLs *only*
for digests it does not already hold — megabytes instead of hundreds of them.

**No archive is written.** The upload is per-file, so a ``.tar.zst`` would be
created only to be read back and thrown away. Uploading from a live ``output/``
tree is therefore the default; an existing bundle is accepted too, for a CI job
that built one in an earlier step.

Around those three calls sits the run lifecycle, and the shape of it is not
arbitrary: a locally-executed run is **one whole-run task that the uploader
claims**. That is what gives the plane's watchdog, cancellation, task counts and
verdict SQL something to operate on, all of which already exist. It is also
exactly what ``steppy ci run`` will do, minus the running::

    POST /v1/runs                      executor=local → run + one kind='run' task
    POST /v1/runs/{id}/claim                          → claim token
    ①②③
    POST /v1/tasks/{tid}/complete                     → the run reaches a verdict
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any, BinaryIO, Callable, Iterator, Optional, Protocol

from ..bundle import BundlePlan, inspect_bundle
from ..bundle.archive import open_archive
from ..bundle.create import MANIFEST_NAME
from ..contracts.bundle import BundleManifest
from .client import CloudClient, CloudError

Opener = Callable[[], BinaryIO]

#: A presigned URL that has expired. S3 and MinIO both answer 403; a malformed
#: one answers 400. Either way the fix is the same — ask for fresh URLs — so
#: they are treated together.
_EXPIRED = frozenset({400, 403})

#: Test outcomes that make a run red. `interrupted` and `worker_died` are
#: included: a test that never finished is not a pass, and reporting one as
#: green is the single worst thing this function could do.
_FAILING_OUTCOMES = frozenset({"failed", "error", "interrupted", "worker_died"})


def derive_exit_code(manifest: BundleManifest) -> int:
    """The run's verdict, read off the manifest.

    Derived rather than observed, because upload happens after the fact and the
    real exit code is long gone. A caller that *does* know it — ``steppy ci
    run``, later — passes it explicitly instead.

    An empty ``tests[]`` yields 0. That is honest (the bundle records no
    failure) but it is also why ``steppy bundle create`` warns when the list is
    empty: without ``steppy run --bundle`` there are no outcomes to read.
    """
    return 1 if any(t.outcome in _FAILING_OUTCOMES for t in manifest.tests) else 0


@dataclass(frozen=True)
class Blob:
    """One file's bytes, addressed by digest and opened on demand."""

    sha256: str
    size: int
    open: Opener


class BlobSource(Protocol):
    """Where the bytes named by a manifest come from."""

    manifest: BundleManifest
    #: 1 when the source can only be read sequentially, so uploads must be
    #: serial. Anything else means the openers are independent and safe to call
    #: from several threads.
    max_concurrency: int

    def blobs(self, wanted: frozenset[str]) -> Iterator[Blob]:
        """Yield each wanted digest exactly once, in whatever order suits."""
        ...


class TreeSource:
    """A live ``output/`` tree, via a :class:`~steppy.bundle.BundlePlan`.

    The default, and the fast one: every blob is a file on disk (or, for the
    two generated members, bytes already in hand), so openers are independent
    and uploads run concurrently.

    Path resolution goes through :func:`~steppy.bundle.archive_members` rather
    than joining ``output_dir`` with each manifest path — ``junit.xml`` and the
    ``events/*.jsonl`` members are generated and have no file under ``output/``
    at all, so a second implementation of the mapping would quietly drop them.
    """

    max_concurrency = 0

    def __init__(self, plan: BundlePlan) -> None:
        self.plan = plan
        self.manifest = plan.manifest
        sha_by_path = {f.path: f.sha256 for f in plan.manifest.files}
        openers: dict[str, Opener] = {}
        for member in plan.members():
            if member.arcname == MANIFEST_NAME:
                # The manifest is the request body, not an uploaded file — and
                # it cannot contain its own digest, so it is not in files[].
                continue
            sha = sha_by_path.get(member.arcname)
            # Two paths can share a digest (an unchanged screenshot captured for
            # two layouts). One upload serves both; first opener wins.
            if sha is None or sha in openers:
                continue
            openers[sha] = self._opener(member)
        self._openers = openers

    @staticmethod
    def _opener(member) -> Opener:
        if member.data is not None:
            data = member.data
            return lambda: BytesIO(data)
        source = member.source
        return lambda: source.open("rb")

    def blobs(self, wanted: frozenset[str]) -> Iterator[Blob]:
        by_sha = {f.sha256: f for f in self.manifest.files}
        for sha in wanted:
            opener = self._openers.get(sha)
            entry = by_sha.get(sha)
            if opener is None or entry is None:
                # The server asked for a digest this bundle does not describe.
                # Impossible unless the manifest and the plan disagree, which is
                # worth failing on rather than uploading something else.
                raise CloudError(f"the server asked for digest {sha[:12]}…, which is not in this bundle")
            yield Blob(sha256=sha, size=entry.size, open=opener)


class ArchiveSource:
    """An existing ``.tar.zst`` / ``.tar.gz`` bundle.

    A zstd bundle is opened as a **stream** (``r|``), so members arrive in
    archive order and only once — which is why uploads from an archive are
    serial, and why each member is buffered in memory for the duration of its
    own PUT. Buffering is what makes a retried upload able to rewind; streaming
    a non-seekable tar member into a retry would resend from wherever the failed
    attempt stopped.

    One member at a time, never the whole archive.
    """

    max_concurrency = 1

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.manifest = inspect_bundle(self.path)

    def blobs(self, wanted: frozenset[str]) -> Iterator[Blob]:
        entries = {f.path: f for f in self.manifest.files if f.sha256 in wanted}
        seen: set[str] = set()
        with open_archive(self.path) as tar:
            for member in tar:
                if not member.isfile():
                    continue
                entry = entries.get(member.name)
                if entry is None or entry.sha256 in seen:
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                data = handle.read()
                seen.add(entry.sha256)
                yield Blob(sha256=entry.sha256, size=entry.size, open=lambda d=data: BytesIO(d))

        missing = wanted - seen
        if missing:
            raise CloudError(
                f"{len(missing)} file(s) the server asked for are missing from {self.path.name}. "
                "Re-create the bundle, or run `steppy bundle verify` on it"
            )


@dataclass
class UploadOutcome:
    """What happened, in the shape the CLI prints and `--json` emits."""

    run_id: str
    run_number: int
    run_status: str
    created_run: bool
    task_id: Optional[str] = None
    #: Blobs the server confirmed were present in the object store.
    verified: int = 0
    total_files: int = 0
    #: Distinct digests among those files. Lower than `total_files` whenever a
    #: screenshot is byte-identical across locales or layouts — and **this**, not
    #: `total_files`, is what `uploaded` and `already_held` are counted against.
    #: The server's `already_held` is in digests while its `total_files` is in
    #: files, so reporting one against the other reads as a discrepancy.
    total_blobs: int = 0
    uploaded: int = 0
    uploaded_bytes: int = 0
    already_held: int = 0
    #: Bytes the content-addressed protocol saved — the whole argument for it,
    #: in numbers the user can see.
    skipped_bytes: int = 0
    payload_bytes: int = 0
    exit_code: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_number": self.run_number,
            "run_status": self.run_status,
            "created_run": self.created_run,
            "task_id": self.task_id,
            "verified": self.verified,
            "total_files": self.total_files,
            "total_blobs": self.total_blobs,
            "uploaded": self.uploaded,
            "uploaded_bytes": self.uploaded_bytes,
            "already_held": self.already_held,
            "skipped_bytes": self.skipped_bytes,
            "payload_bytes": self.payload_bytes,
            "exit_code": self.exit_code,
            "warnings": self.warnings,
        }


def _run_spec(manifest: BundleManifest) -> dict[str, Any]:
    """`BundleSpec` → the plane's `RunSpec`.

    Not a passthrough: the bundle records a single ``tag`` (whatever the run was
    invoked with) while the plane's spec takes a ``tags`` list, and the plane
    carries reporting fields a bundle never sees. `RunSpec` is `extra="forbid"`,
    so sending the bundle's shape verbatim would be a 422.
    """
    spec = manifest.run.spec
    raw_tag = spec.tag
    if raw_tag is None:
        tags: list[str] = []
    elif isinstance(raw_tag, (list, tuple)):
        tags = [str(t) for t in raw_tag]
    else:
        tags = [str(raw_tag)]

    return {
        "scenario": spec.scenario,
        "tags": tags,
        "guide": spec.guide,
        "no_guide": spec.no_guide,
        "no_asserts": spec.no_asserts,
        "screenshots": spec.screenshots,
        "coverage": spec.coverage,
        "locales": list(spec.locales),
        "layouts": list(spec.layouts),
        "workers": spec.workers,
    }


def _scenarios(manifest: BundleManifest) -> list[str]:
    return sorted({t.scenario for t in manifest.tests if t.scenario})


def _create_run(client: CloudClient, manifest: BundleManifest, *, executor: str, trigger: str) -> dict[str, Any]:
    """Create the plane-side run, idempotently.

    The bundle's own ``run_id`` is the idempotency key. It is stable across
    retries of the same upload and unique across runs, which is exactly what
    ``Idempotency-Key`` wants — so a second `steppy bundle upload` after a
    network failure attaches to the run the first one made instead of burning a
    second run number for one set of results.
    """
    payload = {
        "trigger": trigger,
        "executor": executor,
        "spec": _run_spec(manifest),
        "git": {
            "sha": manifest.run.git.sha,
            "branch": manifest.run.git.branch,
            "pr_number": manifest.run.git.pr,
        },
        "scenarios": _scenarios(manifest),
    }
    return client.post("/runs", json=payload, headers={"Idempotency-Key": manifest.run.run_id})


def _claim(client: CloudClient, run_id: str, *, steppy_version: str, runner: str, timeout: float) -> dict[str, Any]:
    """Take ownership of the run's whole-run task.

    Retries while the run is still ``queued``, because dispatch is a background
    job: the run exists the instant ``POST /runs`` answers, but its task is
    written a moment later by a worker. Retrying blind would be wrong — a 409 on
    an already-dispatched run means somebody else owns it — so the run's status
    is what decides whether to wait.
    """
    deadline = time.monotonic() + timeout
    delay = 0.25
    while True:
        try:
            return client.post(
                f"/runs/{run_id}/claim",
                json={"steppy_version": steppy_version, "runner": runner},
            )
        except CloudError as e:
            if e.status != 409:
                raise
            status = (client.get(f"/runs/{run_id}") or {}).get("status")
            if status != "queued":
                raise CloudError(
                    f"run {run_id} is {status!r} and cannot be claimed — another executor owns it, "
                    "or it was cancelled. Upload to a new run, or pass --run-id for a different one",
                    status=409,
                ) from None
            if time.monotonic() >= deadline:
                raise CloudError(
                    f"run {run_id} was still queued after {timeout:.0f}s — the control plane's "
                    "background worker may not be running",
                    status=409,
                ) from None
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


def _offer(client: CloudClient, run_id: str, manifest: BundleManifest, *, token: str) -> dict[str, Any]:
    return client.post(f"/runs/{run_id}/bundle", json=manifest.model_dump(mode="json"), token=token)


@dataclass
class _Sent:
    """Upload progress, counted as it happens rather than inferred afterwards.

    Mutable and shared with :func:`_put_all` on purpose: when a presigned URL
    expires mid-batch the exception carries no partial result, and reconstructing
    "how far did we get" from the next offer is guesswork. This is the one place
    the answer is known.
    """

    count: int = 0
    nbytes: int = 0


def _put_all(
    client: CloudClient,
    source: BlobSource,
    targets: list[dict[str, Any]],
    *,
    concurrency: int,
    sent: _Sent,
    on_progress: Optional[Callable[[Blob], None]],
) -> None:
    """PUT every target, recording progress into *sent* as each one lands."""
    by_sha = {t["sha256"]: t for t in targets}
    lock = Lock()

    def put(blob: Blob) -> None:
        client.put_blob(by_sha[blob.sha256]["url"], blob.open, blob.size)
        with lock:
            sent.count += 1
            sent.nbytes += blob.size
            if on_progress is not None:
                on_progress(blob)

    wanted = frozenset(by_sha)
    if source.max_concurrency == 1 or concurrency <= 1:
        for blob in source.blobs(wanted):
            put(blob)
        return

    # Openers are independent here, so listing them costs nothing — no bytes are
    # read until a worker calls one.
    blobs = list(source.blobs(wanted))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        # list() rather than a bare map: map is lazy, and an exception in a
        # worker would otherwise surface only if someone iterated the result.
        list(pool.map(put, blobs))


def upload_bundle(
    client: CloudClient,
    source: BlobSource,
    *,
    run_id: Optional[str] = None,
    executor: str = "local",
    trigger: str = "manual",
    exit_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    concurrency: int = 4,
    claim_timeout: float = 60.0,
    settle_timeout: float = 30.0,
    on_progress: Optional[Callable[[Blob], None]] = None,
) -> UploadOutcome:
    """Push ``source``'s bundle to the plane and take the run to a verdict."""
    manifest = source.manifest
    created = run_id is None

    run = (
        _create_run(client, manifest, executor=executor, trigger=trigger) if created else client.get(f"/runs/{run_id}")
    )
    run_id = str(run["id"])

    job = _claim(
        client,
        run_id,
        steppy_version=manifest.run.steppy_version,
        runner=f"{executor} upload",
        timeout=claim_timeout,
    )
    token = job["claim_token"]
    task_id = job["task_id"]

    offer = _offer(client, run_id, manifest, token=token)
    sent = _Sent()

    # One re-offer, no more. A URL that expires mid-upload is ordinary on a slow
    # link; the offer is keyed on (run, task) and reports what is already held,
    # so re-offering returns only the remainder and nothing is re-sent. A second
    # expiry means the URL lifetime is shorter than a single blob takes, which
    # is a server setting, not something to retry through.
    for attempt in (0, 1):
        if not offer["uploads"]:
            break
        try:
            _put_all(client, source, offer["uploads"], concurrency=concurrency, sent=sent, on_progress=on_progress)
            break
        except CloudError as e:
            if e.status not in _EXPIRED or attempt == 1:
                raise
            offer = _offer(client, run_id, manifest, token=token)

    finalized = client.post(f"/runs/{run_id}/bundle/finalize", token=token)

    # **Finalize before complete, always.** Completing first would let
    # `close_if_finished` run with no bundle pending, close the run, and then
    # refuse to move it to `ingesting` when the bundle lands — the plane's close
    # statement guards on the run not already being terminal. The results would
    # still be ingested; the run's status would just be a lie.
    resolved_exit = derive_exit_code(manifest) if exit_code is None else exit_code
    client.post(
        f"/tasks/{task_id}/complete",
        json={"exit_code": resolved_exit, "duration_ms": duration_ms},
        token=token,
    )

    status = _settle(client, run_id, timeout=settle_timeout)
    payload_bytes = sum(f.size for f in manifest.files)

    return UploadOutcome(
        run_id=run_id,
        run_number=int(run.get("number", 0)),
        run_status=status,
        created_run=created,
        task_id=task_id,
        verified=int(finalized.get("verified", 0)) if isinstance(finalized, dict) else 0,
        total_files=int(offer.get("total_files", len(manifest.files))),
        total_blobs=len(manifest.digests()),
        uploaded=sent.count,
        uploaded_bytes=sent.nbytes,
        already_held=int(offer.get("already_held", 0)),
        # Derived from what was sent, not from the offer: after a re-offer the
        # offer's `already_held` describes the second round only.
        skipped_bytes=max(0, payload_bytes - sent.nbytes),
        payload_bytes=payload_bytes,
        exit_code=resolved_exit,
        warnings=list(manifest.warnings),
    )


def _settle(client: CloudClient, run_id: str, *, timeout: float) -> str:
    """Wait briefly for ingestion to land the run's verdict.

    Ingestion is a background job, so `complete` leaves the run at `ingesting`
    for a moment. Waiting makes the command's last line the truth rather than a
    waypoint — but only briefly: a slow queue is not this command's failure, and
    the bundle is safely stored either way. Whatever status we have when the
    clock runs out is reported as-is.
    """
    deadline = time.monotonic() + timeout
    status = "unknown"
    while True:
        run = client.get(f"/runs/{run_id}") or {}
        status = str(run.get("status", status))
        if status not in ("ingesting", "running", "queued", "dispatched") or time.monotonic() >= deadline:
            return status
        time.sleep(1.0)
