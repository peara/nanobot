from __future__ import annotations

import logging
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import yaml

from nanobot.config import _deep_merge

if TYPE_CHECKING:
    import asyncpraw

logger = logging.getLogger(__name__)

_SUCCESS_HTML = """<html><body>
<h1>Authorization successful!</h1>
<p>You can close this window.</p>
</body></html>"""

_ERROR_HTML_TEMPLATE = """<html><body>
<h1>Authorization failed</h1>
<p>{error}</p>
</body></html>"""


class _OAuthHandler(BaseHTTPRequestHandler):
    """Captures the OAuth redirect callback on localhost."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "error" in params:
            error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_ERROR_HTML_TEMPLATE.format(error=error).encode())
            self.server.auth_result = {"ok": False, "error": error}  # type: ignore[attr-defined]
            return

        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_SUCCESS_HTML.encode())

        self.server.auth_result = {"ok": True, "code": code, "state": state}  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        pass


class RedditOAuthResult:
    """Holds the Reddit instance and OAuth state during the authorization flow."""

    def __init__(self, reddit: asyncpraw.Reddit, state: str) -> None:
        self.reddit = reddit
        self.state = state


def _check_asyncpraw() -> None:
    try:
        import asyncpraw  # noqa: F401
    except ImportError:
        raise SystemExit(
            "asyncpraw is required for Reddit OAuth bootstrapping. Install it with: pip install asyncpraw"
        ) from None


def _generate_auth_url(
    client_id: str,
    client_secret: str,
    redirect_port: int,
    scopes: list[str],
    user_agent: str,
) -> RedditOAuthResult:
    """Create a Reddit instance and generate the authorization URL."""
    import asyncpraw

    reddit = asyncpraw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=f"http://localhost:{redirect_port}",
        user_agent=user_agent,
    )
    state = secrets.token_urlsafe(32)
    reddit.auth.url(duration="permanent", scopes=scopes, state=state)
    return RedditOAuthResult(reddit=reddit, state=state)


async def _wait_for_callback(port: int, expected_state: str, timeout: float = 300.0) -> dict[str, str]:
    """Start an HTTP server on localhost to receive the OAuth redirect.

    Raises:
        TimeoutError: No callback within timeout.
        ValueError: State mismatch (CSRF).
        RuntimeError: OAuth error in callback params.
    """
    server = HTTPServer(("127.0.0.1", port), _OAuthHandler)
    server.auth_result = None  # type: ignore[attr-defined]
    server.timeout = timeout

    logger.info("Waiting for OAuth callback on http://localhost:%d ...", port)
    server.handle_request()

    result: dict[str, Any] | None = server.auth_result  # type: ignore[attr-defined]
    server.server_close()

    if result is None:
        raise TimeoutError(f"No OAuth callback received within {timeout}s on port {port}")

    if not result.get("ok"):
        raise RuntimeError(f"OAuth authorization failed: {result.get('error', 'unknown')}")

    state = result.get("state")
    if state != expected_state:
        raise ValueError(f"OAuth state mismatch! Expected {expected_state!r}, got {state!r}.")

    code = result.get("code")
    if not code:
        raise RuntimeError("OAuth callback missing authorization code")

    return {"code": str(code)}


def _write_env_file(env_path: str, entries: dict[str, str]) -> None:
    """Write or update a .env file, preserving existing entries and comments."""
    path = Path(env_path)
    existing_lines: list[str] = []
    existing_keys: dict[str, int] = {}

    if path.is_file():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(existing_lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                existing_keys[key] = idx

    for key, value in entries.items():
        env_line = f"{key}={value}"
        if key in existing_keys:
            existing_lines[existing_keys[key]] = env_line
        else:
            existing_lines.append(env_line)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
    logger.info("Updated .env file: %s", env_path)


def _build_reddit_server_config() -> dict[str, Any]:
    """Build the MCP server config dict for the Reddit server."""
    return {
        "name": "reddit",
        "command": "python",
        "args": ["-m", "nanobot.mcp_servers.reddit.server"],
        "required_env": ["PRAW_CLIENT_ID", "PRAW_CLIENT_SECRET", "PRAW_REFRESH_TOKEN"],
        "env": {
            "PRAW_CLIENT_ID": "${PRAW_CLIENT_ID}",
            "PRAW_CLIENT_SECRET": "${PRAW_CLIENT_SECRET}",
            "PRAW_REFRESH_TOKEN": "${PRAW_REFRESH_TOKEN}",
        },
    }


def _write_override_config(config_path: str, reddit_server: dict[str, Any]) -> None:
    """Write or update config.override.yaml with the Reddit MCP server entry.

    Uses _deep_merge so existing override config is preserved and lists are appended.
    """
    override_path = Path(str(config_path).removesuffix(".yaml") + ".override.yaml")
    override_data: dict[str, Any] = {}

    if override_path.is_file():
        existing = yaml.safe_load(override_path.read_text(encoding="utf-8")) or {}
        if isinstance(existing, dict):
            override_data = existing

    new_entry = {"mcp_servers": [reddit_server]}
    merged = _deep_merge(override_data, new_entry)

    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(
        yaml.dump(merged, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("Updated config override: %s", override_path)


async def bootstrap_reddit(
    client_id: str,
    client_secret: str,
    redirect_port: int = 8080,
    scopes: str = "identity,read,submit,edit,privatemessages,history",
    user_agent: str = "nanobot/1.0 by u/YOUR_USERNAME",
    config_path: str = "config.yaml",
) -> str:
    """Bootstrap Reddit OAuth refresh token via interactive OAuth flow.

    Generates auth URL, waits for localhost callback, exchanges code for refresh token,
    then writes .env and config.override.yaml.

    Returns:
        The refresh token string.
    """
    _check_asyncpraw()

    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    if not scope_list:
        raise ValueError("At least one OAuth scope is required")

    oauth = _generate_auth_url(
        client_id=client_id,
        client_secret=client_secret,
        redirect_port=redirect_port,
        scopes=scope_list,
        user_agent=user_agent,
    )

    print(f"Waiting for redirect on http://localhost:{redirect_port} ...")

    callback_result = await _wait_for_callback(port=redirect_port, expected_state=oauth.state)
    code = callback_result["code"]
    refresh_token = await oauth.reddit.auth.authorize(code)

    print("\nRefresh token obtained!\n")

    env_path = str(Path(config_path).parent / ".env")
    _write_env_file(
        env_path,
        {
            "PRAW_CLIENT_ID": client_id,
            "PRAW_CLIENT_SECRET": client_secret,
            "PRAW_REFRESH_TOKEN": refresh_token,
        },
    )

    reddit_server = _build_reddit_server_config()
    _write_override_config(config_path, reddit_server)

    print("Done! Next steps:")
    print("  1. Review .env and config.override.yaml")
    print(f"  2. Start nanobot: python -m nanobot.main --config {config_path}")
    print()

    await oauth.reddit.close()
    return refresh_token
