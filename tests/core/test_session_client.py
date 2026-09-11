"""Mocked Canvas REST calls over the Harvard session path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import canvas_mcp.core.client as client_module
from canvas_mcp.core.session_auth import resolve_auth_material


def _session_config(**overrides: object) -> SimpleNamespace:
    values = {
        "canvas_api_url": "https://canvas.harvard.edu/api/v1",
        "canvas_api_token": "",
        "canvas_session_cookie": "canvas_session=test-session-cookie",
        "chrome_user_data_dir": "",
        "chrome_profile_directory": "",
        "canvas_auth_mode": "session",
        "max_concurrent_requests": 5,
        "api_timeout": 30,
        "log_api_requests": False,
        "enable_data_anonymization": False,
        "anonymization_debug": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestSessionBackedCanvasRequests:
    @pytest.fixture(autouse=True)
    def reset_client_state(self):
        client_module.http_client = None
        client_module._http_client_loop_ref = None
        client_module._request_semaphore = None
        client_module._semaphore_loop_ref = None
        yield
        client_module.http_client = None
        client_module._http_client_loop_ref = None
        client_module._request_semaphore = None
        client_module._semaphore_loop_ref = None

    def _mock_json_client(self, payload):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = payload
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        return mock_client

    @pytest.mark.asyncio
    async def test_users_self_session_json(self):
        payload = {
            "id": 100001,
            "name": "Test Student",
            "login_required": False,
        }
        mock_client = self._mock_json_client(payload)
        with (
            patch(
                "canvas_mcp.core.config.get_config",
                return_value=_session_config(),
            ),
            patch(
                "canvas_mcp.core.client._get_http_client",
                return_value=mock_client,
            ),
        ):
            result = await client_module.make_canvas_request("get", "/users/self")

        assert result["id"] == 100001
        assert result["login_required"] is False
        requested = mock_client.get.await_args.args[0]
        assert requested == "https://canvas.harvard.edu/api/v1/users/self"

    @pytest.mark.asyncio
    async def test_active_courses_session_json(self):
        payload = [{"id": 10, "name": "Example Course", "course_code": "EX-101"}]
        mock_client = self._mock_json_client(payload)
        with (
            patch(
                "canvas_mcp.core.config.get_config",
                return_value=_session_config(),
            ),
            patch(
                "canvas_mcp.core.client._get_http_client",
                return_value=mock_client,
            ),
        ):
            result = await client_module.make_canvas_request(
                "get",
                "/courses",
                params={"enrollment_state": "active"},
            )

        assert result[0]["id"] == 10
        requested = mock_client.get.await_args.args[0]
        assert requested == "https://canvas.harvard.edu/api/v1/courses"
        assert mock_client.get.await_args.kwargs["params"]["enrollment_state"] == "active"

    @pytest.mark.asyncio
    async def test_self_todo_session_json(self):
        payload = [
            {
                "type": "quiz",
                "course_id": 10,
                "assignment": {"name": "Week 1 Quiz", "due_at": "2026-09-15T23:59:00Z"},
            }
        ]
        mock_client = self._mock_json_client(payload)
        with (
            patch(
                "canvas_mcp.core.config.get_config",
                return_value=_session_config(),
            ),
            patch(
                "canvas_mcp.core.client._get_http_client",
                return_value=mock_client,
            ),
        ):
            result = await client_module.make_canvas_request("get", "/users/self/todo")

        assert result[0]["type"] == "quiz"
        assert result[0]["assignment"]["name"] == "Week 1 Quiz"

    @pytest.mark.asyncio
    async def test_login_required_is_an_error(self):
        mock_client = self._mock_json_client({"login_required": True})
        with (
            patch(
                "canvas_mcp.core.config.get_config",
                return_value=_session_config(),
            ),
            patch(
                "canvas_mcp.core.client._get_http_client",
                return_value=mock_client,
            ),
        ):
            result = await client_module.make_canvas_request("get", "/users/self")

        assert "error" in result
        assert "login_required" in result["error"]

    @pytest.mark.asyncio
    async def test_http_client_uses_cookie_not_bearer(self):
        captured = {}

        def fake_async_client(**kwargs):
            captured["headers"] = kwargs["headers"]
            client = AsyncMock()
            client.is_closed = False
            return client

        with patch(
            "canvas_mcp.core.config.get_config",
            return_value=_session_config(),
        ), patch(
            "canvas_mcp.core.client.httpx.AsyncClient",
            side_effect=fake_async_client,
        ):
            client_module._get_http_client()

        headers = captured["headers"]
        assert headers["Cookie"] == "canvas_session=test-session-cookie"
        assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_chrome_profile_uses_same_origin_fetch(self):
        async def fake_fetch(method, url, **kwargs):
            assert method == "get"
            assert url == "https://canvas.harvard.edu/api/v1/users/self"
            assert kwargs["user_data_dir"] == "<chrome-user-data-dir>"
            return 200, '{"id": 100001, "login_required": false}'

        with (
            patch(
                "canvas_mcp.core.config.get_config",
                return_value=_session_config(
                    canvas_session_cookie="",
                    chrome_user_data_dir="<chrome-user-data-dir>",
                ),
            ),
            patch(
                "canvas_mcp.core.client.fetch_via_chrome_profile",
                side_effect=fake_fetch,
            ),
            patch("canvas_mcp.core.client._get_http_client") as mock_http,
        ):
            result = await client_module.make_canvas_request("get", "/users/self")

        mock_http.assert_not_called()
        assert result["id"] == 100001
        assert result["login_required"] is False

    def test_error_strings_do_not_echo_cookie(self):
        from canvas_mcp.core.session_auth import redact_secrets

        leaked = redact_secrets("Request failed: Cookie: canvas_session=test-session-cookie")
        assert "test-session-cookie" not in leaked


class TestAuthMaterialHelpers:
    def test_session_preferred_over_token_in_headers(self):
        from canvas_mcp.core.client import _canvas_auth_headers

        headers = _canvas_auth_headers(
            "test-token-fallback",
            session_cookie="canvas_session=test-session-cookie",
        )
        assert headers["Cookie"] == "canvas_session=test-session-cookie"
        assert "Authorization" not in headers
        assert resolve_auth_material(
            auth_mode="session",
            session_cookie="canvas_session=test-session-cookie",
            api_token="test-token-fallback",
        ).kind == "session_cookie"
