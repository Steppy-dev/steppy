"""A fake Steppy Cloud speaking the upload protocol of :mod:`steppy.contracts.upload`.

Wired in through ``httpx.MockTransport``, which is a real transport: the
client's own header merging, ``Content-Length``, retries and the two-client
split (an upload URL's PUT must carry no ``Authorization``) are all exercised.
Stubbing ``CloudClient`` instead would verify only that the stub was called.

It keeps a blob store per instance, so "only the missing files are uploaded" is
a property of the fake's *storage* rather than of a canned answer, and it parses
every request with the published contract models, the way a server mirroring
them would.
"""

from __future__ import annotations

import hashlib
import json
from typing import Callable, Optional

import httpx

from steppy.cloud import CloudClient, Endpoint
from steppy.contracts.report import parse_report
from steppy.contracts.upload import CreateRunRequest

API = "https://cloud.test/api/v1"
BLOBS = "https://blobs.test"
GOOD_KEY = "stp_good"


def no_sleep(_seconds: float) -> None:
    """Retries back off with a sleep; the tests do not wait for it."""


class FakeCloud:
    def __init__(
        self,
        *,
        wants: Optional[Callable[[dict], bool]] = None,
        expire_puts: int = 0,
        key_status: int = 200,
        unreachable: bool = False,
        fail_with: Optional[int] = None,
        put_headers: Optional[dict[str, str]] = None,
    ) -> None:
        #: Which manifest files the server keeps; every file by default.
        self.wants = wants or (lambda _file: True)
        self.blobs: dict[str, bytes] = {}
        self.runs: dict[str, dict] = {}
        self.by_key: dict[str, str] = {}
        self.calls: list[str] = []
        self.puts: list[str] = []
        self.creates: list[httpx.Request] = []
        self.finalized: dict[str, int] = {}
        self._expire_puts = expire_puts
        self._key_status = key_status
        self._unreachable = unreachable
        self._fail_with = fail_with
        self._put_headers = put_headers or {}

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self, key: str = GOOD_KEY, *, retries: int = 2) -> CloudClient:
        return CloudClient(Endpoint(api=API, key=key), transport=self.transport, retries=retries, sleep=no_sleep)

    def connect(self, endpoint: Endpoint, *, timeout: float = 60.0) -> CloudClient:
        """A drop-in for ``steppy.cloud.connect``, for the CLI tests."""
        return CloudClient(endpoint, timeout=timeout, transport=self.transport, sleep=no_sleep)

    # --- routes -------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if self._unreachable:
            raise httpx.ConnectError("connection refused", request=request)
        if url.startswith(BLOBS):
            return self._put(request)

        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if self._fail_with is not None:
            return httpx.Response(self._fail_with, json={"detail": f"the fake answers {self._fail_with}"})
        if request.headers.get("Authorization") != f"Bearer {GOOD_KEY}":
            return httpx.Response(401, json={"detail": "invalid API key"})

        if request.method == "GET" and path == "/api/v1/key":
            if self._key_status != 200:
                return httpx.Response(self._key_status, json={"detail": "key revoked"})
            return httpx.Response(
                200, json={"project": {"id": "P1", "name": "Shop", "url": "https://cloud.test/p/shop"}}
            )
        if request.method == "POST" and path == "/api/v1/runs":
            return self._create(request)
        if request.method == "POST" and path.startswith("/api/v1/runs/") and path.endswith("/finalize"):
            return self._finalize(path.split("/")[4])
        return httpx.Response(404, json={"detail": f"no route {request.method} {path}"})

    def _missing(self, manifest: dict) -> list[str]:
        wanted = {f["sha256"] for f in manifest["files"] if self.wants(f)}
        return sorted(wanted - set(self.blobs))

    def _create(self, request: httpx.Request) -> httpx.Response:
        self.creates.append(request)
        body = CreateRunRequest.model_validate(json.loads(request.content))
        manifest = body.manifest.model_dump(mode="json")
        key = request.headers.get("Idempotency-Key")
        status = 200
        if key and key in self.by_key:
            run_id = self.by_key[key]
        else:
            run_id = f"R{len(self.runs) + 1}"
            self.runs[run_id] = {"number": len(self.runs) + 1, "manifest": manifest}
            if key:
                self.by_key[key] = run_id
            status = 201
        run = self.runs[run_id]
        uploads = [{"sha256": s, "url": f"{BLOBS}/{s}", "headers": self._put_headers} for s in self._missing(manifest)]
        return httpx.Response(
            status,
            json={
                "run_id": run_id,
                "number": run["number"],
                "url": f"https://cloud.test/r/{run_id}",
                "uploads": uploads,
            },
        )

    def _finalize(self, run_id: str) -> httpx.Response:
        run = self.runs.get(run_id)
        if run is None:
            return httpx.Response(404, json={"detail": "no such run"})
        absent = self._missing(run["manifest"])
        if absent:
            return httpx.Response(409, json={"detail": f"{len(absent)} file(s) were never uploaded"})
        report = next(f for f in run["manifest"]["files"] if f["path"] == "report.json")
        model = parse_report(json.loads(self.blobs[report["sha256"]]))
        self.finalized[run_id] = self.finalized.get(run_id, 0) + 1
        return httpx.Response(
            200,
            json={
                "run_id": run_id,
                "number": run["number"],
                "url": f"https://cloud.test/r/{run_id}",
                "status": model.totals.verdict,
            },
        )

    def _put(self, request: httpx.Request) -> httpx.Response:
        sha = request.url.path.lstrip("/")
        # An upload URL carries its authorisation in the query string. A store
        # that also receives an Authorization header rejects the pair, so this
        # is the assertion that keeps the two-client split honest.
        assert "Authorization" not in request.headers, "an upload URL's PUT must not carry the API key"
        for name, value in self._put_headers.items():
            assert request.headers.get(name) == value, f"the PUT lost the {name} header it was signed with"
        if self._expire_puts:
            self._expire_puts -= 1
            return httpx.Response(403, text="<Error><Code>AccessDenied</Code>Request has expired</Error>")
        data = request.read()
        assert request.headers["Content-Length"] == str(len(data))
        assert hashlib.sha256(data).hexdigest() == sha, "uploaded bytes do not match the digest asked for"
        self.puts.append(sha)
        self.blobs[sha] = data
        return httpx.Response(200)
