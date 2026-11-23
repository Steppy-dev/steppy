"""``steppy.cloud.client``: where the instance is, which key, and what is retried."""

from __future__ import annotations

import httpx
import pytest

from steppy.cloud import CloudAuthError, CloudClient, CloudConfigError, CloudError, CloudUnreachable, Endpoint
from steppy.cloud.client import CONNECT_TIMEOUT, resolve_endpoint

_ENV = ("STEPPY_API_KEY", "STEPPY_URL", "STEPPY_CLOUD_TOKEN", "STEPPY_CLOUD_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def key(monkeypatch) -> None:
    monkeypatch.setenv("STEPPY_API_KEY", "stp_k")


# --- the address --------------------------------------------------------------


def test_steppy_url_is_the_instance_and_gets_api_v1(monkeypatch, key) -> None:
    monkeypatch.setenv("STEPPY_URL", "https://steppy.acme.internal/")
    endpoint = resolve_endpoint()
    assert endpoint.api == "https://steppy.acme.internal/api/v1"
    assert endpoint.url_source == "STEPPY_URL"
    assert endpoint.display == "https://steppy.acme.internal"


def test_the_alias_steppy_cloud_url_keeps_its_meaning(monkeypatch, key) -> None:
    """An API base, to which only /v1 is appended — what it meant before."""
    monkeypatch.setenv("STEPPY_CLOUD_URL", "https://steppy.acme.internal/api")
    endpoint = resolve_endpoint()
    assert endpoint.api == "https://steppy.acme.internal/api/v1"
    assert endpoint.display == "https://steppy.acme.internal"
    monkeypatch.setenv("STEPPY_CLOUD_URL", "https://plane.test/v1/")
    endpoint = resolve_endpoint()
    assert endpoint.api == "https://plane.test/v1" and endpoint.display == "https://plane.test"


def test_address_precedence_is_flag_then_new_name_then_alias_then_config(monkeypatch, key) -> None:
    monkeypatch.setenv("STEPPY_URL", "https://new.test")
    monkeypatch.setenv("STEPPY_CLOUD_URL", "https://alias.test/api")
    config = "https://config.test"

    assert resolve_endpoint("https://flag.test", config_url=config).api == "https://flag.test/api/v1"
    assert resolve_endpoint(config_url=config).api == "https://new.test/api/v1"
    monkeypatch.delenv("STEPPY_URL")
    assert resolve_endpoint(config_url=config).api == "https://alias.test/api/v1"
    monkeypatch.delenv("STEPPY_CLOUD_URL")
    endpoint = resolve_endpoint(config_url=config)
    assert endpoint.api == "https://config.test/api/v1" and endpoint.url_source == "cloud.url"


def test_config_is_read_only_when_nothing_else_named_an_address(monkeypatch, key) -> None:
    """Loading config.yaml prints its own warnings; a --json answer must not
    carry them when the address came from the environment."""
    calls = []
    monkeypatch.setenv("STEPPY_URL", "https://new.test")
    resolve_endpoint(config_url=lambda: calls.append(1) or "https://config.test")
    assert calls == []
    monkeypatch.delenv("STEPPY_URL")
    assert resolve_endpoint(config_url=lambda: "https://config.test").url_source == "cloud.url"


@pytest.mark.parametrize(
    "pasted",
    ["https://steppy.test", "https://steppy.test/", "https://steppy.test/api", "https://steppy.test/api/v1/"],
)
def test_a_pasted_api_url_is_forgiven(pasted: str, key) -> None:
    """A second /api would 404 in a way that reads like an outage."""
    assert resolve_endpoint(pasted).api == "https://steppy.test/api/v1"


def test_an_instance_under_a_path_keeps_its_path(key) -> None:
    assert resolve_endpoint("https://corp.test/tools/steppy").api == "https://corp.test/tools/steppy/api/v1"


def test_an_address_that_is_not_http_is_a_configuration_error(monkeypatch, key) -> None:
    monkeypatch.setenv("STEPPY_URL", "steppy.acme.internal")
    with pytest.raises(CloudConfigError, match="STEPPY_URL must be an http"):
        resolve_endpoint()


def test_no_address_names_where_one_can_come_from(key) -> None:
    with pytest.raises(CloudConfigError) as e:
        resolve_endpoint()
    assert "STEPPY_URL" in str(e.value) and "--url" in str(e.value) and "cloud.url" in str(e.value)
    with pytest.raises(CloudConfigError) as e:
        resolve_endpoint(url_flag=None)
    assert "--url" not in str(e.value)


# --- the key ------------------------------------------------------------------


def test_key_precedence_is_flag_then_new_name_then_alias(monkeypatch) -> None:
    monkeypatch.setenv("STEPPY_URL", "https://new.test")
    monkeypatch.setenv("STEPPY_API_KEY", "stp_new")
    monkeypatch.setenv("STEPPY_CLOUD_TOKEN", "stp_alias")

    assert resolve_endpoint(key="stp_flag").key == "stp_flag"
    endpoint = resolve_endpoint()
    assert endpoint.key == "stp_new" and endpoint.key_source == "STEPPY_API_KEY"
    monkeypatch.delenv("STEPPY_API_KEY")
    endpoint = resolve_endpoint()
    assert endpoint.key == "stp_alias" and endpoint.key_source == "STEPPY_CLOUD_TOKEN"


def test_an_empty_key_is_no_key(monkeypatch) -> None:
    """A CI secret a fork cannot read expands to an empty string."""
    monkeypatch.setenv("STEPPY_URL", "https://new.test")
    monkeypatch.setenv("STEPPY_API_KEY", "  ")
    monkeypatch.setenv("STEPPY_CLOUD_TOKEN", "stp_alias")
    assert resolve_endpoint().key == "stp_alias"
    monkeypatch.delenv("STEPPY_CLOUD_TOKEN")
    with pytest.raises(CloudConfigError, match="no API key"):
        resolve_endpoint()


def test_the_key_never_appears_in_a_repr() -> None:
    """`--verbose` prints tracebacks, and a dataclass repr would put a live
    credential in one."""
    assert "sekrit" not in repr(Endpoint(api="https://x.test/api/v1", key="stp_sekrit"))


# --- the transport ------------------------------------------------------------


def _client(handler, *, retries: int = 2) -> tuple[CloudClient, list[float]]:
    slept: list[float] = []
    client = CloudClient(
        Endpoint(api="https://x.test/api/v1", key="stp_k"),
        transport=httpx.MockTransport(handler),
        retries=retries,
        sleep=slept.append,
    )
    return client, slept


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_overload_and_server_errors_are_retried_twice(status: int) -> None:
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(status, json={"detail": "busy"})

    client, slept = _client(handler)
    with client, pytest.raises(CloudError) as e:
        client.get("/key")
    assert len(calls) == 3 and len(slept) == 2
    assert e.value.status == status


def test_transport_errors_are_retried_twice_then_reported_unreachable() -> None:
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectTimeout("timed out", request=request)

    client, _ = _client(handler)
    with client, pytest.raises(CloudUnreachable, match="could not reach"):
        client.get("/key")
    assert len(calls) == 3


def test_a_retry_that_succeeds_is_a_success() -> None:
    answers = iter([httpx.Response(503), httpx.Response(200, json={"ok": True})])
    client, _ = _client(lambda request: next(answers))
    with client:
        assert client.get("/key") == {"ok": True}


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_never_retried(status: int) -> None:
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, json={"detail": "invalid API key"})

    client, slept = _client(handler)
    with client, pytest.raises(CloudAuthError) as e:
        client.get("/key")
    assert len(calls) == 1 and slept == [], "401/403 means stop, not back off"
    assert e.value.detail == "invalid API key"


def test_a_422_carries_the_servers_detail() -> None:
    detail = "report.json v3 is not accepted (supports v2, v1): upgrade steppy-dev"
    client, slept = _client(lambda request: httpx.Response(422, json={"detail": detail}))
    with client, pytest.raises(CloudError) as e:
        client.post("/runs", json={})
    assert detail in str(e.value) and slept == []


def test_a_validation_422_is_made_readable() -> None:
    body = {"detail": [{"loc": ["body", "manifest", "files", 0, "sha256"], "msg": "Field required", "type": "missing"}]}
    client, _ = _client(lambda request: httpx.Response(422, json=body))
    with client, pytest.raises(CloudError) as e:
        client.post("/runs", json={})
    assert "manifest.files.0.sha256: Field required" in str(e.value)


def test_a_413_says_the_manifest_is_too_large() -> None:
    client, slept = _client(lambda request: httpx.Response(413, json={"detail": "manifest over 50000 files"}))
    with client, pytest.raises(CloudError) as e:
        client.post("/runs", json={})
    assert "too large" in str(e.value) and "--max-size" in str(e.value) and slept == []


def test_retry_after_is_honoured_within_reason() -> None:
    answers = iter(
        [
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(429, headers={"Retry-After": "3600"}),
            httpx.Response(200, json={}),
        ]
    )
    client, slept = _client(lambda request: next(answers))
    with client:
        client.get("/key")
    assert slept == [3.0, 30.0]


def test_the_connect_timeout_is_ten_seconds() -> None:
    client, _ = _client(lambda request: httpx.Response(200))
    with client:
        assert CONNECT_TIMEOUT == 10.0
        assert client._api.timeout.connect == 10.0
        assert client._blobs.timeout.connect == 10.0


def test_a_put_to_an_upload_url_sends_no_key() -> None:
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["length"] = request.headers.get("Content-Length")
        return httpx.Response(204)

    from io import BytesIO

    client, _ = _client(handler)
    with client:
        client.put_blob("https://blobs.test/abc?sig=1", lambda: BytesIO(b"abc"), 3)
    assert seen == {"auth": None, "length": "3"}


def test_an_answer_that_is_not_json_is_an_error() -> None:
    client, _ = _client(lambda request: httpx.Response(200, text="<html>login</html>"))
    with client, pytest.raises(CloudError, match="did not answer with JSON"):
        client.get("/key")
