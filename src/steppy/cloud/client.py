"""HTTP transport for a Steppy control plane.

Knows about bearer tokens, retries and error shapes. Knows nothing about
bundles — that is :mod:`steppy.cloud.upload`'s job, and keeping the two apart is
what will let ``steppy ci run`` and ``steppy agent`` reuse this unchanged.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, Optional

import httpx

#: Environment variables, checked after an explicit flag and before config.
ENV_URL = "STEPPY_CLOUD_URL"
ENV_TOKEN = "STEPPY_CLOUD_TOKEN"

#: The API version this client speaks. Appended to the configured base URL, so a
#: project's `cloud.url` never has to be edited when the server adds v2.
API_VERSION = "v1"

DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 3

#: Retried with backoff. 429 is included because it is the server asking us to
#: slow down, not to stop; 5xx because a control plane behind a load balancer
#: will occasionally return one that a second attempt does not.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class CloudError(Exception):
    """A request the plane refused, or a transport that never got there."""

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class CloudAuthError(CloudError):
    """401 or 403.

    Split out because it is the one failure that must **never** be retried: a
    revoked token retried with backoff is a client that hammers a server it can
    never satisfy, and the same rule governs the agent long-poll ("401 means
    stop, not back off"). It is also the error most likely to be a user's typo,
    so its message names the two places a token can come from.
    """


@dataclass(frozen=True)
class Endpoint:
    """A resolved control plane: where it is, and what proves who we are."""

    url: str
    token: str

    @property
    def api(self) -> str:
        return f"{self.url}/{API_VERSION}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Never let a token reach a traceback, a log, or a --verbose dump.
        return f"Endpoint(url={self.url!r}, token='***')"


def _clean_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    # Forgive the URL people actually have in front of them. `/v1` is what the
    # docs and every error message show, so it is what gets pasted; silently
    # accepting it beats a 404 on `…/v1/v1/runs` that reads like the server is
    # broken.
    if url.endswith(f"/{API_VERSION}"):
        url = url[: -(len(API_VERSION) + 1)]
    return url


def resolve_endpoint(
    url: Optional[str] = None,
    token: Optional[str] = None,
    *,
    config_url: Optional[str] = None,
) -> Endpoint:
    """Resolve the plane's address and credential: flag → env → config.

    **The token has no config source, deliberately.** ``config.yaml`` is
    committed, and a control-plane token is a credential — the same line
    :func:`steppy.config._check_credential_hygiene` already draws for
    ``apps.*.auth.credentials``. Leaving the key out entirely is stronger than
    warning about it, because there is then nowhere to paste it by accident.
    """
    resolved_url = url or os.environ.get(ENV_URL) or config_url
    if not resolved_url:
        raise CloudError(
            f"no control plane URL. Pass --url, set {ENV_URL}, or add `cloud: {{ url: https://… }}` to config.yaml"
        )

    resolved_token = token or os.environ.get(ENV_TOKEN)
    if not resolved_token:
        raise CloudError(
            f"no control plane token. Set {ENV_TOKEN} or pass --token. "
            "There is no config.yaml key for it on purpose — that file is committed"
        )

    return Endpoint(url=_clean_url(resolved_url), token=resolved_token.strip())


def _detail(response: httpx.Response) -> str:
    """The server's own explanation, which is written to be read by a user."""
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:500] or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)
    return str(payload)[:500]


class CloudClient:
    """A session against one plane.

    Two httpx clients, and the second one is not an oversight. Presigned URLs
    carry their authorisation in the query string, and an object store that
    receives a presigned signature **and** an ``Authorization`` header rejects
    the pair as ambiguous — a 400 that reads like a bad URL. So blob uploads go
    out on a client that has no default headers at all.
    """

    def __init__(
        self,
        endpoint: Endpoint,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        user_agent: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        from .. import __version__

        self.endpoint = endpoint
        self._retries = max(0, retries)
        # `transport` exists for tests, and it is worth the parameter: an
        # `httpx.MockTransport` exercises the real client — its header merging,
        # its Content-Length, its retries — where a stubbed CloudClient would
        # verify only that the test's own stub was called.
        self._api = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {endpoint.token}",
                "User-Agent": user_agent or f"steppy/{__version__}",
                "Accept": "application/json",
            },
        )
        # Longer timeout: a blob PUT is bytes over the wire, not a query.
        self._blobs = httpx.Client(timeout=max(timeout, 300.0), transport=transport, headers={})

    def __enter__(self) -> CloudClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._api.close()
        self._blobs.close()

    # --- API calls ----------------------------------------------------------

    def get(self, path: str, *, params: Optional[dict[str, Any]] = None, token: Optional[str] = None) -> Any:
        return self._json("GET", path, params=params, token=token)

    def post(
        self,
        path: str,
        *,
        json: Any = None,
        headers: Optional[dict[str, str]] = None,
        token: Optional[str] = None,
    ) -> Any:
        return self._json("POST", path, json=json, headers=headers, token=token)

    def _json(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        token: Optional[str] = None,
    ) -> Any:
        url = f"{self.endpoint.api}{path}"
        request_headers = dict(headers or {})
        if token is not None:
            # A claim token authorises exactly one task. Swapping it in per
            # request rather than per client keeps one connection pool for the
            # whole upload — and makes the narrower credential the explicit
            # choice at each call site.
            request_headers["Authorization"] = f"Bearer {token}"

        response = self._send(lambda: self._api.request(method, url, json=json, params=params, headers=request_headers))
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as e:
            raise CloudError(f"{method} {path}: the server did not return JSON ({e})") from e

    def put_blob(self, url: str, opener, size: int) -> None:
        """Upload one blob to a presigned URL.

        ``Content-Length`` is set explicitly because it is signed into the URL:
        without it httpx would stream chunked and the store would reject the
        request. It is also what makes a truncated read a rejected upload rather
        than a silently corrupt blob.
        """
        with opener() as handle:
            self._send(
                lambda: self._blobs.put(url, content=handle, headers={"Content-Length": str(size)}),
                rewind=handle,
                # A 403 here is an *expired presigned URL*, not a bad credential —
                # there is no credential on this request. Raising CloudAuthError
                # would send a fixable condition down the "stop, do not retry"
                # path; the caller re-offers instead. See `upload._EXPIRED`.
                auth_fatal=False,
            )

    # --- transport ----------------------------------------------------------

    def _send(self, call, *, rewind: Optional[BinaryIO] = None, auth_fatal: bool = True) -> httpx.Response:
        """Issue a request, retrying only what retrying can fix."""
        last: Optional[Exception] = None
        for attempt in range(self._retries + 1):
            if attempt and rewind is not None:
                # A retried upload must resend from byte zero. Without this the
                # second attempt sends whatever the first one had not yet read,
                # and the store stores a valid-looking truncated blob.
                rewind.seek(0)
            try:
                response = call()
            except httpx.HTTPError as e:
                last = CloudError(f"could not reach the control plane: {e}")
            else:
                if auth_fatal and response.status_code in (401, 403):
                    raise CloudAuthError(_detail(response), status=response.status_code)
                if response.status_code < 400:
                    return response
                error = CloudError(_detail(response), status=response.status_code)
                if response.status_code not in _RETRY_STATUSES:
                    raise error
                last = error

            if attempt < self._retries:
                # Full jitter: N clients that failed together must not retry
                # together.
                time.sleep(random.uniform(0, min(8.0, 0.5 * 2**attempt)))

        assert last is not None
        raise last
