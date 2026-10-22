"""The in-app tour grammar leaves: names, test-id extraction, build ids, routes.

These modules sit on the eager CLI path (``models.py`` imports them) and define
what the compiler may emit; the TypeScript player re-checks the same values.
"""

import shutil
import subprocess

import pytest

from steppy import naming
from steppy.buildid import (
    GIT_SHA_PREFIX_LEN,
    BuildIdError,
    check_build_id_syntax,
    is_build_id,
    parse_build_id_spec,
    resolve_build_id,
)
from steppy.testids import REASONS, check_test_id_attribute, extract_test_id, is_test_id
from steppy.tour_spec import is_route, is_tour_id, tour_file_name

# ── naming ────────────────────────────────────────────────────────────────────


def test_every_product_name_is_derived_from_the_one_constant():
    for value in (
        naming.TOUR_HOST_TAG,
        naming.TOURS_MANIFEST_KIND,
        naming.TOUR_FILE_KIND,
        naming.TOURS_KILL_SWITCH_GLOBAL.lower(),
        naming.TOUR_RESUME_STORAGE_KEY,
        naming.TOUR_PROBE_BINDING,
    ):
        assert naming.PRODUCT in value


def test_the_host_tag_is_a_valid_custom_element_name():
    assert "-" in naming.TOUR_HOST_TAG and naming.TOUR_HOST_TAG == naming.TOUR_HOST_TAG.lower()


# ── test-id extraction ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "selector, expected, reduced",
    [
        ('[data-testid="save-btn"]', "save-btn", None),
        ("[data-testid='save-btn']", "save-btn", None),
        ("[data-testid=save-btn]", "save-btn", None),
        ('  [data-testid = "save-btn"]  ', "save-btn", None),
        ('button[data-testid="save-btn"]', "save-btn", None),
        ('[data-testid="customer-select"] input', "customer-select", "input"),
        ('[data-testid="customer-select"] p-select', "customer-select", "p-select"),
        ('[data-testid="nav/top:menu.item_1"]', "nav/top:menu.item_1", None),
    ],
)
def test_test_id_shapes_are_accepted(selector, expected, reduced):
    got = extract_test_id(selector)
    assert got.ok and got.test_id == expected and got.reduced_from == reduced
    assert got.explain() == ""


def test_match_first_is_a_no_op_but_other_matches_are_not():
    assert extract_test_id('[data-testid="row"]', match="first").test_id == "row"
    assert extract_test_id('[data-testid="row"]', match="First").test_id == "row"
    assert extract_test_id('[data-testid="row"]', match="last").reason == "match"
    assert extract_test_id('[data-testid="row"]', match="2").reason == "match"


@pytest.mark.parametrize(
    "selector, kwargs, reason",
    [
        ('[data-testid="row"]', {"within": '[data-testid="card"]'}, "within"),
        ("", {}, "empty"),
        (None, {}, "empty"),
        ('[data-testid="item-{{id}}"]', {}, "interpolated"),
        ('text="Save"', {}, "playwright-engine"),
        ("//button", {}, "playwright-engine"),
        ('[data-testid="a"] >> nth=0', {}, "playwright-chain"),
        ('[data-testid="a"], [data-testid="b"]', {}, "selector-list"),
        ('[data-testid^="row-"]', {}, "partial-match"),
        ('[data-testid*="row"]', {}, "partial-match"),
        ("#kc-login", {}, "no-test-id"),
        ("button.p-confirmdialog-reject-button", {}, "no-test-id"),
        ('[data-testid="menu"] a[href="/users"]', {}, "qualified-descendant"),
        ('[data-testid="a"] [data-testid="b"]', {}, "qualified-descendant"),
        ('[data-testid="row"]:first-child', {}, "qualified-descendant"),
        ('[data-testid="host"] > button', {}, "qualified-descendant"),
        ('[data-testid="has space"]', {}, "invalid-value"),
        ('[data-testid="' + "x" * 129 + '"]', {}, "invalid-value"),
    ],
)
def test_non_test_id_selectors_are_rejected_with_a_reason(selector, kwargs, reason):
    got = extract_test_id(selector, **kwargs)
    assert not got.ok and got.reason == reason
    why, hint = REASONS[reason]
    assert got.explain() == f"{why} — {hint}"


def test_a_custom_test_id_attribute_is_honoured():
    assert extract_test_id('[data-test="save"]', "data-test").test_id == "save"
    assert extract_test_id('[data-testid="save"]', "data-test").reason == "no-test-id"


def test_test_id_values_use_full_matching():
    assert is_test_id("a/b.c:d_e-f")
    assert not is_test_id("a\n")
    assert not is_test_id("x" * 129)
    assert not is_test_id('[data-testid="x"]')


def test_the_attribute_setting_is_validated():
    assert check_test_id_attribute("data-qa") == "data-qa"
    for bad in ("id", "data-Test", "data_test", "class", 'data-x"]'):
        with pytest.raises(ValueError):
            check_test_id_attribute(bad)


# ── build ids ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["git-sha", "git-sha:../app", "1.4.2", "demo-0001", "a", "a..b"])
def test_valid_build_id_specs_parse(raw):
    parse_build_id_spec(raw)


@pytest.mark.parametrize("raw", ["", "  ", "..", "-x", "x-", "a/b", "con", "x" * 65, "git-sha:", "${APP}"])
def test_invalid_build_id_specs_are_rejected(raw):
    with pytest.raises(BuildIdError):
        parse_build_id_spec(raw)


def test_unresolved_env_refs_pass_the_load_check_but_not_resolution(tmp_path):
    assert check_build_id_syntax("${APP_BUILD_ID}") == "${APP_BUILD_ID}"
    with pytest.raises(BuildIdError, match="environment variable"):
        resolve_build_id("${APP_BUILD_ID}", tmp_path)
    with pytest.raises(BuildIdError):
        check_build_id_syntax("../escape")


def test_a_literal_resolves_to_itself_and_an_override_wins(tmp_path):
    assert resolve_build_id("1.2.3", tmp_path) == "1.2.3"
    assert resolve_build_id("git-sha", tmp_path, override="ci-77") == "ci-77"


def test_is_build_id_never_admits_a_path_segment_escape():
    assert is_build_id("v1.2") and not is_build_id("..") and not is_build_id("a/b") and not is_build_id("NUL")


def test_a_device_name_before_an_extension_is_not_a_build_id():
    # Windows reserves `con.v2` as it does `con`: the directory cannot be created.
    assert not is_build_id("con.v2") and not is_build_id("NUL.1") and not is_build_id("lpt1.x")
    assert is_build_id("v1.con") and is_build_id("con1.x") and is_build_id("console")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_sha_resolves_to_a_fixed_length_prefix_of_head(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t"}
    env["GIT_COMMITTER_EMAIL"] = "t@example.com"
    import os

    run_env = {**os.environ, **env}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=run_env)
    (repo / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True, env=run_env)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=repo, check=True, env=run_env)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()

    assert resolve_build_id("git-sha", repo) == head[:GIT_SHA_PREFIX_LEN]
    # git-sha:<path> reaches a sibling repository from another project root.
    project = tmp_path / "e2e"
    project.mkdir()
    assert resolve_build_id("git-sha:../app", project) == head[:GIT_SHA_PREFIX_LEN]


def test_git_sha_outside_a_repository_explains_the_fix(tmp_path):
    with pytest.raises(BuildIdError, match="literal build id"):
        resolve_build_id("git-sha", tmp_path / "missing")


# ── routes and ids ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "route",
    ["/", "/invoices", "/invoices/:invoiceId", "/a/b-c_d.e~f", "/caf%C3%A9", "/x/:id/edit", "/a%2eb", "/%2e%2e%2e"],
)
def test_valid_routes(route):
    assert is_route(route)


@pytest.mark.parametrize(
    "route",
    [
        "",
        "invoices",
        "//evil.example/x",
        "/\\evil",
        "javascript:alert(1)",
        "https://example.com/x",
        "/a/../b",
        "/a/./b",
        # A URL parser reads %2e as a dot: these climb out of the app's base path.
        "/%2e%2e/admin",
        "/a/%2E",
        "/.%2e/x",
        "/%2e/x",
        "/a?b=1",
        "/a#b",
        "/a b",
        "/%zz",
        "/" + "a/" * 40,
        "/a\n",
    ],
)
def test_unsafe_or_malformed_routes_are_rejected(route):
    assert not is_route(route)


def test_tour_ids_exclude_device_names_and_build_file_names():
    assert is_tour_id("create-invoice") and not is_tour_id("Con") and not is_tour_id("-x")
    assert tour_file_name("create-invoice", "fr-BE") == "create-invoice.fr-BE.json"
