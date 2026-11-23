"""HTTP transport for a Steppy Cloud instance.

Knows about the API key, where the instance is, retries and error shapes. Knows
nothing about runs or bundles — that is :mod:`steppy.cloud.upload`'s job.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable, Optional

import httpx

from ..contracts.upload import API_VERSION

#: The project's API key and the instance's address, as the spec names them.
ENV_KEY = "STEPPY_API_KEY"
ENV_URL = "STEPPY_URL"
#: The earlier names, kept as aliases. ``STEPPY_CLOUD_URL`` keeps its earlier
#: meaning too: the API's base, to which only ``/v1`` is appended.
ENV_KEY_ALIAS = "STEPPY_CLOUD_TOKEN"
ENV_URL_ALIAS = "STEPPY_CLOUD_URL"

#: Where an instance serves its API, under its address.
API_PREFIX = "/api"

#: Per request. A blob PUT gets at least :data:`BLOB_TIMEOUT`.
DEFAULT_TIMEOUT = 60.0
BLOB_TIMEOUT = 300.0
#: How long to wait for a connection, then give up on that attempt.
CONNECT_TIMEOUT = 10.0
#: Retries after the first attempt, for what retrying can fix.
DEFAULT_RETRIES = 2

#: Retried with backoff. 429 is the server asking us to slow down, not to stop;
#: 5xx because an instance behind a load balancer occasionally returns one that
#: a second attempt does not.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Never wait longer than this on a server's ``Retry-After``.
_MAX_RETRY_AFTER = 30.0


class CloudError(Exception):
    """A request the server refused, or a transport that never got there."""

    def __init__(self, message: str, *, status: Optional[int] = None, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


class CloudConfigError(CloudError):
    """No API key, no address, or an address that is not one. Nothing was sent."""


class CloudAuthError(CloudError):
    """401 or 403: the key was refused.

    Split out because it is the one failure that must **never** be retried: a
    revoked key retried with backoff is a client hammering a server it can never
    satisfy. It is also the error most likely to be a user's typo, so the CLI
    names where the key came from.
    """


class CloudUnreachable(CloudError):
    """No answer at all, after the retries: DNS, refused connection, timeout.

    The one failure ``steppy run --upload`` only warns about before the tests,
    because an outage must never break a test run.
    """


@dataclass(frozen=True)
class Endpoint:
    """A resolved instance: where its API is, and the key that proves who we are."""

    #: The API's base, version included (``https://steppy.example.com/api/v1``).
    #: Every path is appended to it.
    api: str
    key: str
    #: Where the address came from (``--url``, ``STEPPY_URL``, ``cloud.url``…).
    url_source: str = ""
    #: Where the key came from (``--api-key``, ``STEPPY_API_KEY``…).
    key_source: str = ""

    @property
    def display(self) -> str:
        """The address to show a person: the instance's, without the API path."""
        for suffix in (f"{API_PREFIX}/{API_VERSION}", f"/{API_VERSION}"):
            if self.api.endswith(suffix):
                return self.api[: -len(suffix)]
        return self.api

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Never let a key reach a traceback, a log, or a --verbose dump.
        return f"Endpoint(api={self.api!r}, key='***', url_source={self.url_source!r})"


def _env(name: str) -> Optional[str]:
    """An environment variable, with an empty or blank value read as unset.

    A CI secret that is not available (a fork's pull request) expands to an
    empty string, and an empty key must read as "no key", not as a key.
    """
    value = os.environ.get(name, "").strip()
    return value or None


def _checked(raw: str, source: str) -> str:
    url = raw.strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise CloudConfigError(f"{source} must be an http(s) address, got {raw.strip()!r}")
    return url


def instance_api(raw: str, source: str = "the address") -> str:
    """An instance's address → its API base: ``<address>/api/v1``.

    Forgives the API URL pasted in its place (``…/api`` or ``…/api/v1``), the
    way an address with a trailing slash is forgiven: appending a second prefix
    would 404 in a way that reads like an outage.
    """
    url = _checked(raw, source)
    for suffix in (f"{API_PREFIX}/{API_VERSION}", API_PREFIX):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return f"{url}{API_PREFIX}/{API_VERSION}"


def base_api(raw: str, source: str = "the address") -> str:
    """``STEPPY_CLOUD_URL``'s meaning: the API's base, to which ``/v1`` is appended."""
    url = _checked(raw, source)
    if url.endswith(f"/{API_VERSION}"):
        url = url[: -(len(API_VERSION) + 1)]
    return f"{url}/{API_VERSION}"


def resolve_endpoint(
    url: Optional[str] = None,
    key: Optional[str] = None,
    *,
    config_url: Optional[str] | Callable[[], Optional[str]] = None,
    url_flag: Optional[str] = "--url",
    key_flag: Optional[str] = "--api-key",
) -> Endpoint:
    """Resolve the instance and the key.

    Address: the flag, ``STEPPY_URL``, ``STEPPY_CLOUD_URL``, then ``cloud.url``
    in ``config.yaml``. Every one of them is the instance's address except
    ``STEPPY_CLOUD_URL``, which keeps its earlier meaning (see :func:`base_api`).
    *config_url* may be a callable, called only when nothing before it gave an
    address: loading ``config.yaml`` prints its own warnings, which must not
    land in a ``--json`` answer that never needed the file.

    Key: the flag, ``STEPPY_API_KEY``, then ``STEPPY_CLOUD_TOKEN``. **The key
    has no config source, deliberately**: ``config.yaml`` is committed, and an
    API key is a credential — the line :func:`steppy.config._check_credential_hygiene`
    draws for ``apps.*.auth.credentials``. With no key there, there is nowhere
    to paste one by accident.

    *url_flag* and *key_flag* name the command's flags in error messages;
    ``None`` for a command that has none.
    """

    def from_config() -> Optional[str]:
        value = config_url() if callable(config_url) else config_url
        return value.strip() if value else None

    candidates: list[tuple[Callable[[], Optional[str]], str, Callable[[str, str], str]]] = [
        (lambda: url.strip() if url else None, url_flag or "--url", instance_api),
        (lambda: _env(ENV_URL), ENV_URL, instance_api),
        (lambda: _env(ENV_URL_ALIAS), ENV_URL_ALIAS, base_api),
        (from_config, "cloud.url", instance_api),
    ]
    found = next(((raw, source, convert) for read, source, convert in candidates if (raw := read())), None)
    if found is None:
        flag = f", pass {url_flag}," if url_flag else ""
        raise CloudConfigError(
            f"no Steppy Cloud address. Set {ENV_URL} to the instance's address{flag} or set cloud.url in config.yaml"
        )
    raw_url, url_source, convert = found
    api = convert(raw_url, url_source)

    keys = [
        (key.strip() if key else None, key_flag or "--api-key"),
        (_env(ENV_KEY), ENV_KEY),
        (_env(ENV_KEY_ALIAS), ENV_KEY_ALIAS),
    ]
    found_key = next(((value, source) for value, source in keys if value), None)
    if found_key is None:
        flag = f" or pass {key_flag}" if key_flag else ""
        raise CloudConfigError(
            f"no API key. Set {ENV_KEY} to the project's key (`stp_…`){flag}. "
            "There is no config.yaml key for it on purpose: that file is committed"
        )
    return Endpoint(api=api, key=found_key[0], url_source=url_source, key_source=found_key[1])


def _detail(response: httpx.Response) -> str:
    """The server's own explanation, which is written to be read by a person."""
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:500] or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            # FastAPI's validation errors: [{loc, msg, type}, …].
            parts = []
            for item in detail[:5]:
                if isinstance(item, dict):
                    loc = ".".join(str(p) for p in item.get("loc", ()) if p != "body")
                    parts.append(f"{loc}: {item.get('msg', item)}" if loc else str(item.get("msg", item)))
                else:
                    parts.append(str(item))
            return "; ".join(parts)
        if detail is not None:
            return str(detail)
    return str(payload)[:500]


def _retry_after(response: Optional[httpx.Response]) -> Optional[float]:
    if response is None:
        return None
    value = response.headers.get("Retry-After", "").strip()
    try:
        seconds = float(value)
    except ValueError:
        return None
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER)


class CloudClient:
    """A session against one instance.

    Two httpx clients, and the second one is not an oversight. An upload URL
    carries its authorisation in its query string, and an object store that
    receives a presigned signature **and** an ``Authorization`` header rejects
    the pair as ambiguous — a 400 that reads like a bad URL. So file uploads go
    out on a client that has no default headers at all.
    """

    def __init__(
        self,
        endpoint: Endpoint,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float = CONNECT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        user_agent: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        from .. import __version__

        self.endpoint = endpoint
        self._retries = max(0, retries)
        self._sleep = sleep
        # `transport` exists for tests, and it is worth the parameter: an
        # `httpx.MockTransport` exercises the real client — its header merging,
        # its Content-Length, its retries — where a stubbed CloudClient would
        # verify only that the test's own stub was called.
        self._api = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            transport=transport,
            headers={
                "Authorization": f"Bearer {endpoint.key}",
                "User-Agent": user_agent or f"steppy/{__version__}",
                "Accept": "application/json",
            },
        )
        self._blobs = httpx.Client(
            timeout=httpx.Timeout(max(timeout, BLOB_TIMEOUT), connect=connect_timeout),
            transport=transport,
            headers={},
        )

    def __enter__(self) -> CloudClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._api.close()
        self._blobs.close()

    # --- API calls ----------------------------------------------------------

    def get(self, path: str) -> Any:
        return self._json("GET", path)

    def post(self, path: str, *, json: Any = None, headers: Optional[dict[str, str]] = None) -> Any:
        return self._json("POST", path, json=json, headers=headers)

    def _json(self, method: str, path: str, *, json: Any = None, headers: Optional[dict[str, str]] = None) -> Any:
        url = f"{self.endpoint.api}{path}"
        response = self._send(
            lambda: self._api.request(method, url, json=json, headers=headers or {}),
            what=f"{method} {path}",
        )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as e:
            raise CloudError(
                f"{method} {path}: the server did not answer with JSON — is {self.endpoint.display} "
                f"a Steppy Cloud instance? ({e})",
                status=response.status_code,
            ) from e

    def put_blob(self, url: str, opener: Callable[[], BinaryIO], size: int, headers: Optional[dict] = None) -> None:
        """Upload one file to its upload URL, with no ``Authorization`` header.

        ``Content-Length`` is set explicitly because it is signed into a
        presigned URL: without it httpx would stream chunked and the store would
        reject the request. It is also what makes a truncated read a rejected
        upload rather than a silently corrupt file.
        """
        sent_headers = {**(headers or {}), "Content-Length": str(size)}
        with opener() as handle:
            self._send(
                lambda: self._blobs.put(url, content=handle, headers=sent_headers),
                what="PUT <upload url>",
                rewind=handle,
                # A 403 here is an *expired upload URL*, not a refused key —
                # there is no key on this request. Raising CloudAuthError would
                # send a fixable condition down the "stop, do not retry" path;
                # the caller asks for fresh URLs instead.
                auth_fatal=False,
            )

    # --- transport ----------------------------------------------------------

    def _send(
        self,
        call: Callable[[], httpx.Response],
        *,
        what: str,
        rewind: Optional[BinaryIO] = None,
        auth_fatal: bool = True,
    ) -> httpx.Response:
        """Issue a request, retrying only what retrying can fix."""
        last: Optional[CloudError] = None
        for attempt in range(self._retries + 1):
            if attempt and rewind is not None:
                # A retried upload must resend from byte zero. Without this the
                # second attempt sends whatever the first one had not yet read,
                # and the store stores a valid-looking truncated file.
                rewind.seek(0)
            response: Optional[httpx.Response] = None
            try:
                response = call()
            except httpx.HTTPError as e:
                last = CloudUnreachable(f"could not reach {self.endpoint.display} ({what}): {e or type(e).__name__}")
            else:
                status = response.status_code
                if status < 400:
                    return response
                detail = _detail(response)
                if auth_fatal and status in (401, 403):
                    raise CloudAuthError(f"{what} → HTTP {status}: {detail}", status=status, detail=detail)
                if status == 413:
                    raise CloudError(
                        f"{what} → HTTP 413: the server refused the request as too large ({detail}). "
                        "`steppy bundle upload` can send fewer files: --no-guides, --no-videos, "
                        "--no-traces or --max-size",
                        status=status,
                        detail=detail,
                    )
                error = CloudError(f"{what} → HTTP {status}: {detail}", status=status, detail=detail)
                if status not in _RETRY_STATUSES:
                    raise error
                last = error

            if attempt < self._retries:
                # Full jitter: N clients that failed together must not retry
                # together. A server's Retry-After wins, within reason.
                delay = _retry_after(response)
                self._sleep(delay if delay is not None else random.uniform(0, min(8.0, 1.0 * 2**attempt)))

        assert last is not None
        raise last
