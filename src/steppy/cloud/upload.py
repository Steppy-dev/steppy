"""Upload one run to a Steppy Cloud instance: the protocol of :mod:`steppy.contracts.upload`.

Three calls, all with the project's API key::

    ① POST /v1/runs                   the manifest, report.json among its files
                                      → one upload URL per wanted, missing file
    ② PUT  <upload url>               the bytes, straight to storage
    ③ POST /v1/runs/{id}/finalize     → the run's URL

Step ① is the whole point. With locales x layouts x device scale factors an
unchanged screenshot dominates a run, and the server only keeps what it wants
(by default the screenshots of failed variants), so it answers with URLs *only*
for the files it wants and does not hold yet — megabytes instead of hundreds of
them.

The ``Idempotency-Key`` of ① is the run's own id, which makes the protocol
resumable without bookkeeping: an interrupted upload run again gets the same
run back with what is still missing, and an upload URL that expired mid-batch
is replaced by asking once more.

**No archive is written.** The upload is per file, so a ``.tar.zst`` would be
created only to be read back and thrown away. Uploading from a live ``output/``
tree is therefore the default; an existing bundle is accepted too, for a CI job
that built one in an earlier step.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any, BinaryIO, Callable, Iterator, Optional, Protocol

from ..bundle import BundlePlan, inspect_bundle
from ..bundle.archive import open_archive
from ..bundle.create import MANIFEST_NAME
from ..contracts.bundle import BundleFile, BundleManifest
from ..contracts.upload import (
    IDEMPOTENCY_HEADER,
    REPORT_KIND,
    REPORT_PATH,
    CreateRunRequest,
    CreateRunResponse,
    FinalizeResponse,
    KeyResponse,
    UploadTarget,
)
from .client import CloudClient, CloudError

Opener = Callable[[], BinaryIO]

#: An upload URL that has expired. S3 and MinIO both answer 403; a malformed
#: one answers 400. Either way the fix is the same — ask for fresh URLs — so
#: they are treated together.
_EXPIRED = frozenset({400, 403})


def check_key(client: CloudClient) -> KeyResponse:
    """``GET /v1/key``: the project the key belongs to. Raises on a refused key."""
    payload = client.get("/key")
    try:
        return KeyResponse.model_validate(payload)
    except ValueError as e:
        raise CloudError(f"GET /key: the answer is not a project ({e})") from e


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
    generated members, bytes already in hand), so openers are independent and
    uploads run concurrently.

    Path resolution goes through :func:`~steppy.bundle.archive_members` rather
    than joining ``output_dir`` with each manifest path — ``junit.xml`` and the
    ``events/*.jsonl`` members are generated and have no file under ``output/``
    at all, so a second implementation of the mapping would quietly drop them.
    """

    max_concurrency = 0

    def __init__(self, plan: BundlePlan, *, output_dir: Optional[Path] = None) -> None:
        self.plan = plan
        self.output_dir = output_dir
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
        for sha in sorted(wanted):
            opener = self._openers.get(sha)
            entry = by_sha.get(sha)
            if opener is None or entry is None:
                # The server asked for a digest this run does not describe.
                # Impossible unless the manifest and the plan disagree, which is
                # worth failing on rather than uploading something else.
                raise CloudError(f"the server asked for digest {sha[:12]}…, which is not in this run")
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


class ReportedSource:
    """A source with the run's ``report.json`` added: what is actually uploaded.

    The report is a file like the others (:mod:`steppy.contracts.upload`): an
    entry in the manifest at :data:`~steppy.contracts.upload.REPORT_PATH`, and
    bytes the server may ask for. It exists only in memory — neither the
    ``output/`` tree nor an archive holds it.
    """

    def __init__(self, inner: BlobSource, report: bytes) -> None:
        from ..bundle.hashing import sha256_bytes

        self.inner = inner
        self.report = report
        self.report_entry = BundleFile(
            path=REPORT_PATH, kind=REPORT_KIND, sha256=sha256_bytes(report), size=len(report)
        )
        files = [f for f in inner.manifest.files if f.path != REPORT_PATH] + [self.report_entry]
        files.sort(key=lambda f: f.path)
        self.manifest = inner.manifest.model_copy(update={"files": files})
        self.max_concurrency = inner.max_concurrency

    def blobs(self, wanted: frozenset[str]) -> Iterator[Blob]:
        entry = self.report_entry
        if entry.sha256 in wanted:
            data = self.report
            yield Blob(sha256=entry.sha256, size=entry.size, open=lambda: BytesIO(data))
        rest = wanted - {entry.sha256}
        if rest:
            yield from self.inner.blobs(frozenset(rest))


def attach_report(source: TreeSource | ArchiveSource, *, project_root: Path):
    """Build the run's ``report.json`` and add it to *source*.

    Returns ``(ReportedSource, ReportModel)``. Built by the same code as
    ``steppy report`` (``build_model``), with captures referenced by their
    manifest path rather than copied into a site.

    Built from ``source.manifest``, never from the archive's own copy: a
    ``bundle upload <archive>`` with metadata flags has overridden it
    (:func:`~steppy.bundle.override_manifest`), and ``report.json``'s run must
    be the one the manifest sent says.
    """
    import dataclasses

    from ..contracts.report import report_bytes
    from ..report.build import build_document
    from ..report.source import from_plan, open_source

    if isinstance(source, TreeSource):
        report_source = from_plan(source.plan, project_root=project_root, output_dir=source.output_dir)
    else:
        report_source = dataclasses.replace(
            open_source(source.path, project_root=project_root), manifest=source.manifest
        )
    model = build_document(report_source, project_root=project_root)
    return ReportedSource(source, report_bytes(model)), model


@dataclass
class UploadOutcome:
    """What happened, in the shape the CLI prints and ``--json`` emits."""

    run_id: str
    number: Optional[int] = None
    #: The run's page, from finalize (or create, when finalize gave none).
    url: Optional[str] = None
    #: The run's status after finalize, as the server reports it.
    status: Optional[str] = None
    #: Manifest entries, ``report.json`` included.
    total_files: int = 0
    #: Distinct digests among those files. Lower than `total_files` whenever a
    #: screenshot is byte-identical across locales or layouts — and **this**, not
    #: `total_files`, is what `requested` and `uploaded` are counted against.
    total_blobs: int = 0
    #: Digests the server asked for: wanted, and not held yet.
    requested: int = 0
    uploaded: int = 0
    uploaded_bytes: int = 0
    payload_bytes: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "number": self.number,
            "url": self.url,
            "status": self.status,
            "total_files": self.total_files,
            "total_blobs": self.total_blobs,
            "requested": self.requested,
            "uploaded": self.uploaded,
            "uploaded_bytes": self.uploaded_bytes,
            "payload_bytes": self.payload_bytes,
            "warnings": self.warnings,
        }


@dataclass
class _Sent:
    """Upload progress, counted as it happens rather than inferred afterwards.

    Mutable and shared with :func:`_put_all` on purpose: when an upload URL
    expires mid-batch the exception carries no partial result, and
    reconstructing "how far did we get" from the next create is guesswork. This
    is the one place the answer is known.
    """

    count: int = 0
    nbytes: int = 0


def _create(client: CloudClient, manifest: BundleManifest) -> CreateRunResponse:
    """``POST /v1/runs``, keyed by the run's own id.

    That id is stable across retries of one upload and unique across runs, which
    is exactly what ``Idempotency-Key`` wants: a second upload after a network
    failure gets the run the first one made, instead of a second run for one set
    of results.
    """
    body = CreateRunRequest(manifest=manifest).model_dump(mode="json", exclude_none=False)
    payload = client.post("/runs", json=body, headers={IDEMPOTENCY_HEADER: manifest.run.run_id})
    try:
        return CreateRunResponse.model_validate(payload)
    except ValueError as e:
        raise CloudError(f"POST /runs: the answer is not a run ({e})") from e


def _put_all(
    client: CloudClient,
    source: BlobSource,
    targets: list[UploadTarget],
    *,
    concurrency: int,
    sent: _Sent,
    on_progress: Optional[Callable[[Blob], None]],
) -> None:
    """PUT every target, recording progress into *sent* as each one lands."""
    by_sha = {t.sha256: t for t in targets}
    lock = Lock()

    def put(blob: Blob) -> None:
        target = by_sha[blob.sha256]
        client.put_blob(target.url, blob.open, blob.size, headers=target.headers)
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


def upload_run(
    client: CloudClient,
    source: BlobSource,
    *,
    concurrency: int = 4,
    on_progress: Optional[Callable[[Blob], None]] = None,
) -> UploadOutcome:
    """Create the run, send what the server asks for, finalize it.

    *source* must already carry ``report.json`` (:func:`attach_report`): the
    server ingests the run from it and refuses to finalize without it.
    """
    manifest = source.manifest
    known = {f.sha256 for f in manifest.files}

    created = _create(client, manifest)
    run_id = created.run_id
    requested = {t.sha256 for t in created.uploads}
    sent = _Sent()

    # One re-create, no more. A URL that expires mid-upload is ordinary on a
    # slow link; creating the same run again returns only what is still
    # missing, so nothing already sent is sent twice. A second expiry means the
    # URL lifetime is shorter than a single file takes, which is a server
    # setting, not something to retry through.
    for attempt in (0, 1):
        targets = created.uploads
        unknown = {t.sha256 for t in targets} - known
        if unknown:
            raise CloudError(
                f"the server asked for {len(unknown)} digest(s) this run does not describe ({sorted(unknown)[0][:12]}…)"
            )
        if not targets:
            break
        try:
            _put_all(client, source, targets, concurrency=concurrency, sent=sent, on_progress=on_progress)
            break
        except CloudError as e:
            if e.status not in _EXPIRED or attempt == 1:
                raise
            created = _create(client, manifest)
            if created.run_id != run_id:
                raise CloudError(
                    f"creating the run again returned another run ({created.run_id}, not {run_id}): "
                    "the server ignored the Idempotency-Key"
                ) from e

    payload = client.post(f"/runs/{run_id}/finalize")
    try:
        finalized = FinalizeResponse.model_validate(payload or {})
    except ValueError as e:
        raise CloudError(f"POST /runs/{run_id}/finalize: the answer is not a run ({e})") from e

    return UploadOutcome(
        run_id=run_id,
        number=finalized.number if finalized.number is not None else created.number,
        url=finalized.url or created.url,
        status=finalized.status,
        total_files=len(manifest.files),
        total_blobs=len(known),
        requested=len(requested),
        uploaded=sent.count,
        uploaded_bytes=sent.nbytes,
        payload_bytes=sum(f.size for f in manifest.files),
        warnings=list(manifest.warnings),
    )
