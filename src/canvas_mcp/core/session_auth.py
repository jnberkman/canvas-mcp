"""Harvard Key browser-session auth for Canvas REST ``/api/v1``.

Primary path: inject a runtime-supplied ``Cookie`` header into httpx.
Optional path: Playwright persistent Chrome profile for same-origin ``fetch``.

Neither path scrapes a browser cookie database. Cookie and token values are
never written to logs; use :func:`redact_secrets` and :func:`describe_auth`
for any user-visible text.

Hypothesis (verified on a signed-in School-box Chrome session, not on this
Cloud VM): Canvas ``/api/v1`` accepts the logged-in Harvard Key session the
same way the HTML UI does — a ``Cookie`` header, no ``Authorization: Bearer``.
If a response has ``login_required: true`` or is HTML rather than JSON, treat
that request as unauthenticated and discard the hypothesis for that call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode, urlparse, urlunparse

from .logging import log_debug, log_warning

AuthKind = Literal["session_cookie", "chrome_profile", "token", "none"]
VALID_AUTH_MODES = frozenset({"session", "token", "auto"})
DEFAULT_AUTH_MODE = "session"

# Names only — never log values parsed from these cookies.
_CSRF_COOKIE_NAMES = frozenset({"_csrf_token", "csrf_token"})

_SECRET_HEADER_RE = re.compile(
    r"(?i)\b(authorization|cookie|x-csrf-token|x-canvas-token|x-canvas-cookie)"
    r"\s*[:=]\s*.+"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(CANVAS_SESSION_COOKIE|CANVAS_API_TOKEN|CANVAS_CHROME_USER_DATA_DIR|"
    r"session_cookie|api_token|csrf_token)\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class CanvasAuthMaterial:
    """Resolved Canvas auth. Values must not be logged."""

    kind: AuthKind
    session_cookie: str = ""
    api_token: str = ""
    chrome_user_data_dir: str = ""
    chrome_profile_directory: str = ""


def redact_secrets(text: str) -> str:
    """Strip cookie/token assignments from an error or log string."""
    if not text:
        return text
    redacted = _SECRET_HEADER_RE.sub(r"\1: [REDACTED]", text)
    return _SECRET_ASSIGNMENT_RE.sub(r"\1=[REDACTED]", redacted)


def csrf_token_from_cookie_header(cookie_header: str) -> str:
    """Return a CSRF token embedded in a Cookie header, or empty.

    Canvas cookie-authenticated writes often need ``X-CSRF-Token``. The value
    is not logged.
    """
    if not cookie_header:
        return ""
    for part in cookie_header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name in _CSRF_COOKIE_NAMES and value:
            return value
    return ""


def describe_auth(material: CanvasAuthMaterial) -> str:
    """Non-secret description suitable for ``--config`` and startup logs."""
    if material.kind == "session_cookie":
        return "session cookie (configured)"
    if material.kind == "chrome_profile":
        return "chrome profile (configured)"
    if material.kind == "token":
        return "api token (optional fallback, configured)"
    return "none"


def resolve_auth_material(
    *,
    auth_mode: str,
    session_cookie: str = "",
    api_token: str = "",
    chrome_user_data_dir: str = "",
    chrome_profile_directory: str = "",
) -> CanvasAuthMaterial:
    """Choose session cookie, Chrome profile, or optional token.

    ``session`` (default) and ``auto`` prefer cookie, then Chrome profile, then
    token. ``token`` uses only ``CANVAS_API_TOKEN``.
    """
    mode = (auth_mode or DEFAULT_AUTH_MODE).strip().lower()
    if mode not in VALID_AUTH_MODES:
        mode = DEFAULT_AUTH_MODE

    cookie = session_cookie.strip()
    token = api_token.strip()
    chrome_dir = chrome_user_data_dir.strip()
    profile = chrome_profile_directory.strip()

    if mode == "token":
        if token:
            return CanvasAuthMaterial(kind="token", api_token=token)
        return CanvasAuthMaterial(kind="none")

    if cookie:
        return CanvasAuthMaterial(kind="session_cookie", session_cookie=cookie)
    if chrome_dir:
        return CanvasAuthMaterial(
            kind="chrome_profile",
            chrome_user_data_dir=chrome_dir,
            chrome_profile_directory=profile,
        )
    if token:
        return CanvasAuthMaterial(kind="token", api_token=token)
    return CanvasAuthMaterial(kind="none")


def resolve_auth_from_config(config: Any) -> CanvasAuthMaterial:
    """Resolve auth from a :class:`~canvas_mcp.core.config.Config`."""
    return resolve_auth_material(
        auth_mode=getattr(config, "canvas_auth_mode", DEFAULT_AUTH_MODE),
        session_cookie=getattr(config, "canvas_session_cookie", ""),
        api_token=getattr(config, "canvas_api_token", ""),
        chrome_user_data_dir=getattr(config, "chrome_user_data_dir", ""),
        chrome_profile_directory=getattr(config, "chrome_profile_directory", ""),
    )


def resolve_auth_from_request(api_token: str, session_cookie: str) -> CanvasAuthMaterial:
    """Resolve per-request HTTP credentials. Session cookie wins when both set."""
    if session_cookie.strip():
        return CanvasAuthMaterial(
            kind="session_cookie", session_cookie=session_cookie.strip()
        )
    if api_token.strip():
        return CanvasAuthMaterial(kind="token", api_token=api_token.strip())
    return CanvasAuthMaterial(kind="none")


def build_canvas_headers(material: CanvasAuthMaterial) -> dict[str, str]:
    """Build request headers. Session is primary; token is the fallback."""
    from .. import __version__

    headers = {
        "User-Agent": (
            f"canvas-mcp/{__version__} "
            "(https://github.com/vishalsachdev/canvas-mcp)"
        ),
        "Accept": "application/json",
    }
    if material.kind == "session_cookie" and material.session_cookie:
        headers["Cookie"] = material.session_cookie
        headers["X-Requested-With"] = "XMLHttpRequest"
        csrf = csrf_token_from_cookie_header(material.session_cookie)
        if csrf:
            headers["X-CSRF-Token"] = csrf
        return headers
    if material.kind == "token" and material.api_token:
        headers["Authorization"] = f"Bearer {material.api_token}"
    return headers


def session_is_unauthenticated(payload: Any) -> bool:
    """True when Canvas reports the caller still owes a login."""
    return isinstance(payload, dict) and payload.get("login_required") is True


async def fetch_via_chrome_profile(
    method: str,
    url: str,
    *,
    user_data_dir: str,
    profile_directory: str = "",
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    form_body: dict[str, Any] | list[tuple[str, Any]] | None = None,
    timeout_ms: int = 30000,
) -> tuple[int, str]:
    """Same-origin ``fetch`` inside a persistent Chrome profile.

    Reuses the box browser profile. Does not read cookie files off disk.
    Requires the optional ``browser`` extra (Playwright).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Chrome profile auth requires the optional browser extra. "
            "Install with: pip install 'canvas-mcp[browser]' && playwright install chrome. "
            "Or set CANVAS_SESSION_COOKIE instead of CANVAS_CHROME_USER_DATA_DIR."
        ) from exc

    if params:
        parsed = urlparse(url)
        query = urlencode(params, doseq=True)
        if parsed.query:
            query = f"{parsed.query}&{query}"
        url = urlunparse(parsed._replace(query=query))

    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    launch_args: list[str] = []
    if profile_directory:
        launch_args.append(f"--profile-directory={profile_directory}")

    log_debug("Canvas request via Chrome persistent profile (no cookie values logged)")

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir,
            channel="chrome",
            headless=True,
            args=launch_args,
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(f"{origin}/", wait_until="domcontentloaded", timeout=timeout_ms)
            result = await page.evaluate(
                """async ({method, url, jsonBody, formBody}) => {
                    const init = {method: method.toUpperCase(), credentials: 'include',
                                  headers: {Accept: 'application/json',
                                            'X-Requested-With': 'XMLHttpRequest'}};
                    if (jsonBody !== null && jsonBody !== undefined) {
                        init.headers['Content-Type'] = 'application/json';
                        init.body = JSON.stringify(jsonBody);
                    } else if (formBody !== null && formBody !== undefined) {
                        init.headers['Content-Type'] = 'application/x-www-form-urlencoded';
                        init.body = new URLSearchParams(formBody).toString();
                    }
                    const resp = await fetch(url, init);
                    const text = await resp.text();
                    return {status: resp.status, body: text};
                }""",
                {
                    "method": method,
                    "url": url,
                    "jsonBody": json_body,
                    "formBody": form_body,
                },
            )
        finally:
            await context.close()

    status = int(result.get("status", 0))
    body = result.get("body")
    if not isinstance(body, str):
        body = "" if body is None else json.dumps(body)
    if status >= 400:
        log_warning(
            "Chrome-profile Canvas request returned an error status",
            status_code=status,
        )
    return status, body
