"""Unit tests for Harvard Key session auth helpers."""

from pathlib import Path

from canvas_mcp.core.session_auth import (
    build_canvas_headers,
    csrf_token_from_cookie_header,
    describe_auth,
    redact_secrets,
    resolve_auth_material,
    session_is_unauthenticated,
)


class TestResolveAuthMaterial:
    def test_session_cookie_is_primary(self):
        material = resolve_auth_material(
            auth_mode="session",
            session_cookie="canvas_session=test-session-cookie",
            api_token="test-token-fallback",
        )
        assert material.kind == "session_cookie"
        assert material.session_cookie == "canvas_session=test-session-cookie"
        assert material.api_token == ""

    def test_chrome_profile_when_no_cookie(self):
        material = resolve_auth_material(
            auth_mode="session",
            chrome_user_data_dir="<chrome-user-data-dir>",
            chrome_profile_directory="<profile-directory>",
        )
        assert material.kind == "chrome_profile"
        assert material.chrome_user_data_dir == "<chrome-user-data-dir>"
        assert material.chrome_profile_directory == "<profile-directory>"

    def test_token_is_optional_fallback(self):
        material = resolve_auth_material(
            auth_mode="session",
            api_token="test-token-fallback",
        )
        assert material.kind == "token"
        assert material.api_token == "test-token-fallback"

    def test_token_mode_ignores_cookie(self):
        material = resolve_auth_material(
            auth_mode="token",
            session_cookie="canvas_session=test-session-cookie",
            api_token="test-token-fallback",
        )
        assert material.kind == "token"

    def test_none_when_empty(self):
        material = resolve_auth_material(auth_mode="session")
        assert material.kind == "none"

    def test_invalid_mode_defaults_to_session(self):
        material = resolve_auth_material(
            auth_mode="not-a-mode",
            session_cookie="canvas_session=test-session-cookie",
        )
        assert material.kind == "session_cookie"


class TestBuildCanvasHeaders:
    def test_session_cookie_has_no_bearer(self):
        headers = build_canvas_headers(
            resolve_auth_material(
                auth_mode="session",
                session_cookie="canvas_session=test-session-cookie",
            )
        )
        assert headers["Cookie"] == "canvas_session=test-session-cookie"
        assert "Authorization" not in headers
        assert headers["Accept"] == "application/json"
        assert "User-Agent" in headers

    def test_token_still_sends_bearer(self):
        headers = build_canvas_headers(
            resolve_auth_material(auth_mode="token", api_token="test-token-fallback")
        )
        assert headers["Authorization"] == "Bearer test-token-fallback"
        assert "Cookie" not in headers

    def test_csrf_copied_from_cookie_without_appearing_in_describe(self):
        cookie = "canvas_session=test-session-cookie; _csrf_token=test-csrf"
        headers = build_canvas_headers(
            resolve_auth_material(auth_mode="session", session_cookie=cookie)
        )
        assert headers["X-CSRF-Token"] == "test-csrf"
        description = describe_auth(
            resolve_auth_material(auth_mode="session", session_cookie=cookie)
        )
        assert "test-csrf" not in description
        assert "test-session-cookie" not in description
        assert description == "session cookie (configured)"


class TestRedactSecrets:
    def test_redacts_cookie_and_token_assignments(self):
        raw = (
            "Cookie: canvas_session=test-session-cookie "
            "CANVAS_API_TOKEN=test-token-fallback"
        )
        redacted = redact_secrets(raw)
        assert "test-session-cookie" not in redacted
        assert "test-token-fallback" not in redacted
        assert "[REDACTED]" in redacted

    def test_describe_auth_never_includes_values(self):
        material = resolve_auth_material(
            auth_mode="session",
            session_cookie="canvas_session=super-secret",
        )
        assert "super-secret" not in describe_auth(material)


class TestSessionUnauthenticated:
    def test_login_required_true(self):
        assert session_is_unauthenticated({"id": 1, "login_required": True}) is True

    def test_login_required_false(self):
        assert session_is_unauthenticated({"id": 1, "login_required": False}) is False

    def test_missing_flag(self):
        assert session_is_unauthenticated({"id": 1}) is False


class TestCsrfParse:
    def test_empty(self):
        assert csrf_token_from_cookie_header("") == ""

    def test_named_cookie(self):
        assert (
            csrf_token_from_cookie_header("a=1; _csrf_token=abc; b=2") == "abc"
        )


class TestNoCookieDbScrape:
    def test_session_auth_module_does_not_mention_sqlite_or_cookie_files(self):
        source = Path("src/canvas_mcp/core/session_auth.py").read_text()
        assert "sqlite" not in source.lower()
        assert "cookie jar" not in source.lower()
        assert "Local Storage" not in source
        # The design must not open Chrome's on-disk cookie database.
        assert "chrome_cookies" not in source.lower()
        assert "user_data_dir" in source  # profile reuse is allowed
        banned = ("cookies sqlite", "network/cookies", "decrypt cookie")
        lowered = source.lower()
        for phrase in banned:
            assert phrase not in lowered
