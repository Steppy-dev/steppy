"""Route/request normalization: deterministic URL -> pattern rules."""

import pytest

from steppy.coverage.normalize import (
    assign_api,
    classify_request,
    compile_extra_patterns,
    match_template,
    normalize_route,
    strip_query,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # dynamic-segment clustering
        ("http://localhost:4200/invoices/123", "/invoices/:id"),
        ("/invoices/456", "/invoices/:id"),
        ("/orders/0a1b2c3d-4e5f-6789-abcd-ef0123456789/lines/7", "/orders/:id/lines/:id"),
        ("/files/3f8a9b2c1d4e5f6a7b8c9d0e1f2a3b4c", "/files/:id"),
        ("/planning/2026-06-11", "/planning/:id"),
        # word segments are NOT clustered
        ("/settings/profile", "/settings/profile"),
        ("/files/abc123", "/files/abc123"),  # short hex-ish token stays
        # query/fragment handling
        ("http://x/orders?page=2&sort=asc", "/orders"),
        ("http://x/orders#section", "/orders"),
        # hash-based SPA routing: fragment IS the route
        ("http://x/#/customers/42", "/customers/:id"),
        ("http://x/index.html#/customers/42?tab=1", "/customers/:id"),
        # roots and trailing slashes
        ("http://x/", "/"),
        ("http://x", "/"),
        ("/orders/", "/orders"),
    ],
)
def test_normalize_route(url, expected):
    assert normalize_route(url) == expected


def test_normalize_route_extra_patterns():
    extra = compile_extra_patterns([r"^INV-\d{4}-\d+$"])
    assert normalize_route("/invoices/INV-2026-0042", extra) == "/invoices/:id"
    assert normalize_route("/invoices/INV-2026-0042") == "/invoices/INV-2026-0042"


def test_strip_query():
    assert strip_query("/api/orders/5?expand=lines#x") == "/api/orders/5"


_APIS = [
    {"name": "editor-api", "base_urls": ["http://localhost:8080", "http://editor-backend:8080"]},
    {"name": "editor-api-v2", "base_urls": ["http://localhost:8080/v2"]},
]


def test_assign_api_host_match():
    assert assign_api("http://localhost:8080/api/users/5", _APIS) == ("editor-api", "/api/users/5")


def test_assign_api_internal_url_origin():
    # Worker-recorded URLs carry Docker-internal hosts.
    assert assign_api("http://editor-backend:8080/api/users", _APIS) == ("editor-api", "/api/users")


def test_assign_api_longest_prefix_wins():
    assert assign_api("http://localhost:8080/v2/users", _APIS) == ("editor-api-v2", "/users")


def test_assign_api_prefix_is_segment_aligned():
    # base .../v2 must not match /v2something
    assert assign_api("http://localhost:8080/v2something/users", _APIS) == (
        "editor-api",
        "/v2something/users",
    )


def test_assign_api_no_match():
    assert assign_api("https://cdn.example.com/lib.js", _APIS) is None


def test_assign_api_base_path_exact():
    assert assign_api("http://localhost:8080/v2", _APIS) == ("editor-api-v2", "/")


def test_classify_request_api_call():
    assert classify_request("get", "http://localhost:8080/api/orders/12", "xhr", _APIS) == {
        "api": "editor-api",
        "method": "GET",
        "path": "/api/orders/12",
    }


def test_classify_request_asset_ignored():
    assert classify_request("GET", "http://localhost:4200/main.js", "script", _APIS) is None


def test_classify_request_unmatched_kept_unclassified():
    out = classify_request("POST", "https://api.thirdparty.io/track", "fetch", _APIS)
    assert out == {"api": "", "method": "POST", "path": "/track"}


def test_classify_request_non_http_ignored():
    assert classify_request("GET", "data:text/plain,hi", "fetch", _APIS) is None


@pytest.mark.parametrize(
    ("path", "templates", "expected"),
    [
        ("/users/5", ["/users/{id}", "/users/me"], "/users/{id}"),
        ("/users/me", ["/users/{id}", "/users/me"], "/users/me"),  # most-literal wins
        ("/users/5/orders", ["/users/{id}"], None),  # segment count mismatch
        ("/orders/7", ["/orders/:orderId"], "/orders/:orderId"),  # :param style
        ("/orders/7?x=1", ["/orders/{id}"], "/orders/{id}"),  # query stripped
        ("/", ["/"], "/"),
        ("/unknown", [], None),
    ],
)
def test_match_template(path, templates, expected):
    assert match_template(path, templates) == expected
