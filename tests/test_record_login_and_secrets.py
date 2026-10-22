"""Tests for `steppy record` secret handling: login-collapse + placeholderize.

The recorder must never bake secrets into a draft: a username/password/submit
sequence collapses to ``- action: login`` (credentials flow from config), and
other detected secret fields become ``${VAR}`` env refs + screenshot masks.
"""

from steppy.record_cli import codegen_source_to_scenario


def _wrap(body: list[str]) -> str:
    lines = "".join(f"    {line}\n" for line in body)
    return "from playwright.sync_api import Page, expect\n\n\ndef test_example(page: Page) -> None:\n" + lines


def _tx(src: str, **kw):
    kw.setdefault("role", "admin")
    kw.setdefault("title", "T")
    return codegen_source_to_scenario(src, **kw)


LOGIN_SRC = _wrap(
    [
        'page.goto("https://a.co/login")',
        'page.get_by_label("Email").fill("user@example.com")',
        'page.get_by_label("Password").fill("s3cr3t-pw")',
        'page.get_by_role("button", name="Sign in").click()',
        'page.get_by_test_id("home").click()',
    ]
)


# ── Login collapse ───────────────────────────────────────────────────────────


def test_login_collapses_when_form_mode_with_creds():
    r = _tx(LOGIN_SRC, base_url="https://a.co", auth_mode="form", has_credentials=True)
    md = r.markdown
    assert "- action: login" in md
    assert "s3cr3t-pw" not in md  # password never baked
    assert "auth: false" in md
    # navigate, login, home-click — the 2 login fills + submit collapsed away.
    assert [ln for ln in md.splitlines() if ln.startswith("- action:")] == [
        "- action: navigate",
        "- action: login",
        "- action: click",
    ]


def test_no_collapse_without_credentials():
    r = _tx(LOGIN_SRC, base_url="https://a.co", auth_mode="form", has_credentials=False)
    md = r.markdown
    assert "- action: login" not in md
    assert "s3cr3t-pw" not in md  # still never baked — placeholderized instead
    assert "${STEPPY_ADMIN_PASSWORD}" in md


def test_no_collapse_when_not_form_mode():
    r = _tx(LOGIN_SRC, base_url="https://a.co", auth_mode="bypass", has_credentials=True)
    md = r.markdown
    assert "- action: login" not in md
    assert "${STEPPY_ADMIN_PASSWORD}" in md


def test_login_collapse_adds_password_mask():
    r = _tx(LOGIN_SRC, base_url="https://a.co", auth_mode="form", has_credentials=True)
    assert "input[type='password']" in r.mask_selectors


# ── Placeholderize secret fields ─────────────────────────────────────────────


def test_secret_field_placeholderized_and_masked():
    r = _tx(_wrap(['page.get_by_test_id("api-token").fill("tok_live_123")']))
    assert "${STEPPY_ADMIN_API_TOKEN}" in r.markdown
    assert "tok_live_123" not in r.markdown
    assert '[data-testid="api-token"]' in r.mask_selectors
    assert "STEPPY_ADMIN_API_TOKEN" in r.env_vars


def test_non_secret_value_stays_literal():
    r = _tx(_wrap(['page.get_by_test_id("full-name").fill("Alice Martin")']))
    assert '- value: "Alice Martin"' in r.markdown
    assert not r.env_vars
    assert not r.mask_selectors


def test_placeholderize_all_replaces_every_value():
    r = _tx(
        _wrap(
            [
                'page.get_by_test_id("full-name").fill("Alice")',
                'page.get_by_test_id("city").fill("Gent")',
            ]
        ),
        placeholderize_all=True,
    )
    assert "Alice" not in r.markdown
    assert "Gent" not in r.markdown
    assert "${STEPPY_ADMIN_FULL_NAME}" in r.markdown
    assert "${STEPPY_ADMIN_CITY}" in r.markdown


def test_env_name_collision_gets_suffix():
    # Two secret fields both reduce to the same token → the second is suffixed.
    r = _tx(
        _wrap(
            [
                'page.get_by_test_id("token").fill("a")',
                'page.get_by_test_id("token").fill("b")',
            ]
        ),
    )
    assert "STEPPY_ADMIN_TOKEN" in r.env_vars
    assert "STEPPY_ADMIN_TOKEN_2" in r.env_vars
