"""Authentication management for Steppy."""

import os
import stat
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from playwright.sync_api import Browser, BrowserContext, Page

from .models import SteppyConfig, AppConfig
from .totp import _totp_now


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


def _fill_totp_if_configured(page: Page, form, credentials) -> None:
    """Fill a TOTP/MFA code after the password submit, when configured (FLAWS §6.2).

    No-op unless both a ``totp_selector`` (on ``auth.form``) and a ``totp_secret``
    (on the app credentials) are set. The selector wait tolerates absence with a
    short timeout so a non-MFA session (already trusted device) falls straight
    through. ``totp_submit_selector`` defaults to the form's ``submit_selector``.
    """
    totp_selector = getattr(form, "totp_selector", None)
    totp_secret = getattr(credentials, "totp_secret", None)
    if not totp_selector or not totp_secret:
        return
    try:
        page.wait_for_selector(totp_selector, timeout=10000)
    except Exception:
        return  # no MFA prompt this session — nothing to fill
    page.fill(totp_selector, _totp_now(totp_secret))
    submit = getattr(form, "totp_submit_selector", None) or form.submit_selector
    page.click(submit)


def get_storage_state_path(role: str, auth_dir: Path) -> Path:
    """Get the path to the storage state file for a role."""
    return auth_dir / f"{role}.json"


def _restrict_permissions(path: Path) -> None:
    """Best-effort chmod 0o600 on a saved storage-state file.

    ``.auth/<role>.json`` holds cleartext cookies + JWTs, so restrict it to the
    owner. A no-op on platforms (e.g. Windows) where POSIX modes don't apply;
    failures are swallowed so auth never breaks over a permissions quirk.
    """
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass


def is_storage_state_valid(role: str, auth_dir: Path, ttl: int) -> bool:
    """
    Check if a cached storage state exists and is still valid.

    Args:
        role: Role name (e.g., "editor", "reviewer")
        auth_dir: Directory containing storage state files
        ttl: Time to live in seconds

    Returns:
        True if valid cache exists, False otherwise
    """
    state_path = get_storage_state_path(role, auth_dir)

    if not state_path.exists():
        return False

    age = time.time() - state_path.stat().st_mtime
    return age < ttl


def authenticate_auth0(
    browser: Browser,
    role: str,
    app_config: AppConfig,
    config: SteppyConfig,
) -> BrowserContext:
    """
    Authenticate using Auth0 and save storage state.

    Args:
        browser: Playwright browser instance
        role: Role name
        app_config: App configuration for this role
        config: Global Steppy configuration

    Returns:
        Authenticated browser context

    Raises:
        AuthenticationError: If authentication fails
    """
    auth_dir = Path(config.auth.storage_state_dir)
    state_path = get_storage_state_path(role, auth_dir)

    # Check cache first
    if is_storage_state_valid(role, auth_dir, config.auth.storage_state_ttl):
        return browser.new_context(
            storage_state=str(state_path),
            viewport={"width": config.screenshot.viewport.width, "height": config.screenshot.viewport.height},
            base_url=app_config.url,
        )

    # Fresh login required — guard the missing-credentials case with a clear,
    # non-wrapped message before launching the browser (FLAWS §5.4).
    if not app_config.auth or not app_config.auth.credentials:
        raise AuthenticationError(
            f"Form login for role '{role}' requires apps.{role}.auth.credentials (username/password)."
        )

    context = browser.new_context(
        viewport={"width": config.screenshot.viewport.width, "height": config.screenshot.viewport.height},
        base_url=app_config.url,
    )
    page = context.new_page()

    try:
        # Navigate to app → triggers Auth0 redirect
        page.goto("/", timeout=30000)

        # Wait for the hosted login page
        form = config.auth.form
        if not form:
            raise AuthenticationError("Login-form configuration (auth.form) is missing")

        page.wait_for_selector(form.username_selector, timeout=15000)

        # Fill credentials
        page.fill(form.username_selector, app_config.auth.credentials.username)
        page.fill(form.password_selector, app_config.auth.credentials.password)
        page.click(form.submit_selector)

        # Optional TOTP/MFA step (no-op unless configured)
        _fill_totp_if_configured(page, form, app_config.auth.credentials)

        # Handle optional consent screen
        if form.consent_selector:
            try:
                page.wait_for_selector(form.consent_selector, timeout=3000)
                page.click(form.consent_selector)
            except Exception:
                pass  # No consent screen, continue

        # Wait for redirect back to the app
        page.wait_for_url(f"{app_config.url}/**", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=30000)

        # Cache the authenticated state
        auth_dir.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        _restrict_permissions(state_path)

        page.close()
        return context

    except Exception as e:
        page.close()
        context.close()
        raise AuthenticationError(f"Form authentication failed for {role}: {e}")


# Preferred name for the generic hosted-login form-fill (FLAWS §6.2); the
# historical ``authenticate_auth0`` name is kept as the implementation.
authenticate_form = authenticate_auth0


def authenticate_storage_state(
    browser: Browser,
    app_config: AppConfig,
    config: SteppyConfig,
) -> BrowserContext:
    """
    Load authentication from an existing storage state file.

    Args:
        browser: Playwright browser instance
        app_config: App configuration
        config: Global Steppy configuration

    Returns:
        Authenticated browser context

    Raises:
        AuthenticationError: If storage state file doesn't exist
    """
    state_path = app_config.auth.storage_state_path
    if not state_path or not Path(state_path).exists():
        raise AuthenticationError(f"Storage state file not found: {state_path}")

    return browser.new_context(
        storage_state=state_path,
        viewport={"width": config.screenshot.viewport.width, "height": config.screenshot.viewport.height},
        base_url=app_config.url,
    )


def authenticate_bypass(
    browser: Browser,
    app_config: AppConfig,
    config: SteppyConfig,
) -> BrowserContext:
    """
    Create context with bypass authentication (no real login).

    Args:
        browser: Playwright browser instance
        app_config: App configuration
        config: Global Steppy configuration

    Returns:
        Browser context with injected auth credentials
    """
    context = browser.new_context(
        viewport={"width": config.screenshot.viewport.width, "height": config.screenshot.viewport.height},
        base_url=app_config.url,
    )

    bypass = app_config.auth.bypass
    if not bypass:
        return context

    # Inject cookie if configured
    if bypass.cookie:
        context.add_cookies([bypass.cookie])

    # Inject localStorage if configured
    if bypass.local_storage:
        page = context.new_page()
        page.goto(app_config.url)
        for key, value in bypass.local_storage.items():
            # Pass key/value as arguments rather than interpolating into the JS
            # source, so a value containing a quote or backslash can't break or
            # inject into the script.
            page.evaluate("([k, v]) => localStorage.setItem(k, v)", [key, value])
        page.close()

    return context


def authenticate_token(
    browser: Browser,
    role: str,
    app_config: AppConfig,
    config: SteppyConfig,
) -> BrowserContext:
    """
    Authenticate by calling a token endpoint and injecting the JWT.

    Args:
        browser: Playwright browser instance
        role: Role name
        app_config: App configuration
        config: Global Steppy configuration

    Returns:
        Authenticated browser context

    Raises:
        AuthenticationError: If token retrieval fails
    """
    token_config = app_config.auth.token
    if not token_config:
        raise AuthenticationError("Token configuration is missing")

    # Call the token endpoint
    try:
        response = httpx.request(
            method=token_config.method,
            url=token_config.endpoint,
            json=token_config.body,
            timeout=10.0,
        )
        response.raise_for_status()
        token_data = response.json()

        # Extract token (assume it's in the response root or in a "token" field)
        token = token_data.get("token") or token_data.get("access_token") or token_data

        if not isinstance(token, str):
            raise AuthenticationError(f"Token endpoint returned invalid data: {token_data}")

    except Exception as e:
        raise AuthenticationError(f"Failed to retrieve token for {role}: {e}")

    # Create context and inject token
    context = browser.new_context(
        viewport={"width": config.screenshot.viewport.width, "height": config.screenshot.viewport.height},
        base_url=app_config.url,
    )

    inject_as = token_config.inject_as
    key = token_config.key

    if inject_as == "localStorage":
        page = context.new_page()
        page.goto(app_config.url)
        # Pass key/token as arguments (not string-interpolated) — the token is
        # network-sourced and could contain a quote that would otherwise break
        # or inject into the JS.
        page.evaluate("([k, v]) => localStorage.setItem(k, v)", [key, token])
        page.close()
    elif inject_as == "cookie":
        # Extract the host safely — urlparse tolerates ports/paths and won't
        # IndexError on a URL missing "://" (FLAWS §5.4).
        domain = urlparse(app_config.url).hostname
        if not domain:
            raise AuthenticationError(
                f"Cannot derive a cookie domain from apps.{role}.url "
                f"({app_config.url!r}); expected a URL like https://host[:port]."
            )
        context.add_cookies(
            [
                {
                    "name": key,
                    "value": token,
                    "domain": domain,
                    "path": "/",
                }
            ]
        )
    elif inject_as == "header":
        # Inject the token as a default request header on the context. `key` is
        # the header name (e.g. "Authorization"); the token endpoint should
        # return the full header value (e.g. "Bearer <jwt>") when a scheme is
        # required (FLAWS §6.2).
        context.set_extra_http_headers({key: token})

    return context


def get_authenticated_context(
    browser: Browser,
    role: str,
    config: SteppyConfig,
) -> BrowserContext:
    """
    Get an authenticated browser context for a role using the configured auth method.

    Args:
        browser: Playwright browser instance
        role: Role name (e.g., "editor", "reviewer")
        config: Global Steppy configuration

    Returns:
        Authenticated browser context

    Raises:
        AuthenticationError: If authentication fails
        ValueError: If role or auth mode is unknown
    """
    if role not in config.apps:
        raise ValueError(f"Unknown role: {role}. Available roles: {list(config.apps.keys())}")

    app_config = config.apps[role]
    mode = app_config.auth.mode

    if mode in ("form", "auth0"):
        return authenticate_auth0(browser, role, app_config, config)
    elif mode == "storage-state":
        return authenticate_storage_state(browser, app_config, config)
    elif mode == "bypass":
        return authenticate_bypass(browser, app_config, config)
    elif mode == "token":
        return authenticate_token(browser, role, app_config, config)
    else:
        raise ValueError(f"Unknown auth mode: {mode}")


def interactive_login(
    role: str,
    config: SteppyConfig,
    browser: Browser,
) -> None:
    """
    Open a headed browser and wait for the user to log in manually.
    Save the storage state when done.

    Args:
        role: Role name
        config: Global Steppy configuration
        browser: Playwright browser instance (should be headed)

    Raises:
        ValueError: If role is unknown
    """
    if role not in config.apps:
        raise ValueError(f"Unknown role: {role}. Available roles: {list(config.apps.keys())}")

    app_config = config.apps[role]
    auth_dir = Path(config.auth.storage_state_dir)
    state_path = get_storage_state_path(role, auth_dir)

    context = browser.new_context(
        viewport={"width": config.screenshot.viewport.width, "height": config.screenshot.viewport.height},
        base_url=app_config.url,
    )
    page = context.new_page()

    # Navigate to the app
    page.goto("/", timeout=60000)

    # Wait for user to log in - just wait for the URL to change from login page
    # This is a simple heuristic - user should close the browser when done
    print(f"\nPlease log in to {app_config.name} in the browser window.")
    print("Press Enter when you have successfully logged in...")
    input()

    # Save storage state
    auth_dir.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(state_path))
    _restrict_permissions(state_path)

    page.close()
    context.close()


def clear_storage_state(role: Optional[str], auth_dir: Path) -> list[str]:
    """
    Clear cached storage state(s).

    Args:
        role: Role name to clear, or None to clear all
        auth_dir: Directory containing storage state files

    Returns:
        List of cleared role names
    """
    cleared = []

    if role:
        # Clear specific role
        state_path = get_storage_state_path(role, auth_dir)
        if state_path.exists():
            state_path.unlink()
            cleared.append(role)
    else:
        # Clear all
        for state_file in auth_dir.glob("*.json"):
            role_name = state_file.stem
            state_file.unlink()
            cleared.append(role_name)

    return cleared


def get_storage_state_status(config: SteppyConfig) -> dict[str, dict]:
    """
    Get status of all storage states.

    Args:
        config: Global Steppy configuration

    Returns:
        Dict mapping role names to status info (exists, age, valid)
    """
    auth_dir = Path(config.auth.storage_state_dir)
    ttl = config.auth.storage_state_ttl

    status = {}
    for role in config.apps.keys():
        state_path = get_storage_state_path(role, auth_dir)

        if state_path.exists():
            age = time.time() - state_path.stat().st_mtime
            valid = age < ttl
            status[role] = {
                "exists": True,
                "age_seconds": int(age),
                "age_minutes": int(age / 60),
                "valid": valid,
                "path": str(state_path),
            }
        else:
            status[role] = {
                "exists": False,
                "age_seconds": None,
                "age_minutes": None,
                "valid": False,
                "path": str(state_path),
            }

    return status
