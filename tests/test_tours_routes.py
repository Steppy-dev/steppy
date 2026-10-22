"""Entry routes (``steppy.tours.routes``): base-relative, param-aware, safe."""

import pytest

from steppy.tours.routes import app_base, bind, matches, parse_entry_pattern, route_from_target, target_segments

ROOT = app_base("http://localhost:3000")
SUB = app_base("https://example.com/app/")


@pytest.mark.parametrize(
    "target, base, route",
    [
        ("/invoices", ROOT, "/invoices"),
        ("/invoices/", ROOT, "/invoices"),
        ("/", ROOT, "/"),
        ("invoices", ROOT, "/invoices"),
        ("http://localhost:3000/invoices", ROOT, "/invoices"),
        ("/app/invoices", SUB, "/invoices"),
        ("invoices", SUB, "/invoices"),
        ("/app", SUB, "/"),
        ("https://example.com/app/x", SUB, "/x"),
        ("/invoices/{{invoice_id}}", ROOT, "/invoices/:invoice_id"),
        ("/café", ROOT, "/caf%C3%A9"),
    ],
)
def test_navigation_targets_become_base_relative_routes(target, base, route):
    result = route_from_target(target, base)
    assert result.error is None and result.route == route


@pytest.mark.parametrize(
    "target, base",
    [
        ("/x#frag", ROOT),
        ("https://evil.example/x", ROOT),
        ("//evil.example/x", ROOT),
        ("/elsewhere", SUB),
        ("/invoices/x{{id}}", ROOT),
    ],
)
def test_unusable_targets_are_errors(target, base):
    assert route_from_target(target, base).error


def test_a_query_is_dropped_and_ids_are_flagged():
    result = route_from_target("/invoices/1001?tab=lines", ROOT)
    assert result.route == "/invoices/1001" and result.query_dropped and result.id_like == ["1001"]


def test_an_unresolved_app_url_reads_routes_from_the_root():
    base = app_base("${APP_URL}")
    assert base.unresolved and route_from_target("/x", base).route == "/x"
    assert route_from_target("https://example.com/x", base).error


def test_explicit_patterns_match_and_bind():
    pattern = parse_entry_pattern("/invoices/:invoiceId/lines")
    assert pattern.route == "/invoices/:invoiceId/lines"
    target = target_segments("/invoices/1001/lines", ROOT)
    assert matches(pattern.segments, target)
    assert bind(pattern.segments, target) == (("invoiceId", "1001"),)
    templated = target_segments("/invoices/{{id}}/lines", ROOT)
    assert matches(pattern.segments, templated) and bind(pattern.segments, templated) == (("invoiceId", "{{id}}"),)
    assert not matches(pattern.segments, target_segments("/invoices/1001", ROOT))
    assert not matches(parse_entry_pattern("/invoices/new").segments, templated[:2])


def test_explicit_patterns_reject_unsafe_segments():
    assert parse_entry_pattern("/a/../b").error
    # Literal segments are percent-encoded the way location.pathname spells them
    # (the frontmatter validator already refuses whitespace in tour.entry).
    assert parse_entry_pattern("/café").route == "/caf%C3%A9"
