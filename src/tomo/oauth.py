from __future__ import annotations

import base64
import hashlib
import http.server
import queue
import secrets
import socket
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .token_store import TokenSet, load_tokens, save_tokens


XAI_API_BASE_URL = "https://api.x.ai/v1"
XAI_OAUTH_ISSUER = "https://auth.x.ai"
XAI_OAUTH_AUTHORIZE_URL = f"{XAI_OAUTH_ISSUER}/oauth2/authorize"
XAI_OAUTH_DISCOVERY_URL = f"{XAI_OAUTH_ISSUER}/.well-known/openid-configuration"
XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_OAUTH_REDIRECT_HOST = "127.0.0.1"
XAI_OAUTH_REDIRECT_PORT = 56121
XAI_OAUTH_REDIRECT_PATH = "/callback"


@dataclass
class Discovery:
    authorization_endpoint: str
    token_endpoint: str


@dataclass
class PkcePair:
    verifier: str
    challenge: str


def generate_pkce() -> PkcePair:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return PkcePair(verifier=verifier, challenge=challenge)


def validate_xai_endpoint(value: str, field: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise RuntimeError(f"xAI OAuth discovery returned a non-HTTPS {field}: {value}")
    host = parsed.hostname or ""
    if host != "x.ai" and not host.endswith(".x.ai"):
        raise RuntimeError(f"xAI OAuth discovery {field} host {host} is not on xAI origin")
    return value


def discover_oauth() -> Discovery:
    response = httpx.get(XAI_OAUTH_DISCOVERY_URL, headers={"Accept": "application/json"}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    authorization_endpoint = str(payload.get("authorization_endpoint", "")).strip()
    token_endpoint = str(payload.get("token_endpoint", "")).strip()
    if not authorization_endpoint or not token_endpoint:
        raise RuntimeError("xAI OIDC discovery did not include authorization and token endpoints")
    return Discovery(
        authorization_endpoint=validate_xai_endpoint(authorization_endpoint, "authorization_endpoint"),
        token_endpoint=validate_xai_endpoint(token_endpoint, "token_endpoint"),
    )


def _free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((XAI_OAUTH_REDIRECT_HOST, preferred))
            return preferred
        except OSError:
            sock.bind((XAI_OAUTH_REDIRECT_HOST, 0))
            return int(sock.getsockname()[1])


def build_authorize_url(redirect_uri: str, code_challenge: str, state: str, nonce: str) -> str:
    params = {
        "response_type": "code",
        "client_id": XAI_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": XAI_OAUTH_SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "nonce": nonce,
        "plan": "generic",
        "referrer": "hermes-agent",
    }
    return f"{XAI_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def wait_for_callback(port: int, expected_state: str, timeout_seconds: int = 180) -> str:
    result: dict[str, str] = {}
    event = threading.Event()
    manual_codes: queue.Queue[str] = queue.Queue(maxsize=1)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            error = params.get("error", [""])[0]
            code = params.get("code", [""])[0]
            state = params.get("state", [""])[0]
            if parsed.path != XAI_OAUTH_REDIRECT_PATH:
                self.send_response(404)
                self.end_headers()
                return
            if error:
                result["error"] = params.get("error_description", [error])[0]
            elif state != expected_state:
                result["error"] = "OAuth state mismatch"
            elif not code:
                result["error"] = "Missing authorization code"
            else:
                result["code"] = code
            self.send_response(200 if "code" in result else 400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"You can close this tab and return to Tomo.")
            event.set()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer((XAI_OAUTH_REDIRECT_HOST, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    input_thread = threading.Thread(target=_read_manual_code, args=(manual_codes, event), daemon=True)
    input_thread.start()
    try:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if event.wait(timeout=0.25):
                break
            try:
                manual_code = manual_codes.get_nowait().strip()
            except queue.Empty:
                continue
            if manual_code:
                result["code"] = manual_code
                event.set()
                break
        else:
            raise TimeoutError("Timed out waiting for OAuth callback or manual code")
    finally:
        server.shutdown()
        thread.join(timeout=5)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["code"]


def _read_manual_code(manual_codes: queue.Queue[str], event: threading.Event) -> None:
    if not sys.stdin.isatty():
        return
    print("If the browser cannot reach Tomo, paste the code from xAI here and press Enter:")
    try:
        code = input().strip()
    except EOFError:
        return
    if code and not event.is_set():
        try:
            manual_codes.put_nowait(code)
        except queue.Full:
            return


def exchange_code(token_endpoint: str, code: str, redirect_uri: str, pkce: PkcePair) -> TokenSet:
    started_at = time.time()
    response = httpx.post(
        validate_xai_endpoint(token_endpoint, "token_endpoint"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": XAI_OAUTH_CLIENT_ID,
            "code_verifier": pkce.verifier,
            "code_challenge": pkce.challenge,
            "code_challenge_method": "S256",
        },
        timeout=30,
    )
    return _parse_token_response(response, token_endpoint, redirect_uri, started_at)


def refresh_tokens(tokens: TokenSet) -> TokenSet:
    started_at = time.time()
    response = httpx.post(
        validate_xai_endpoint(tokens.token_endpoint, "token_endpoint"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data={
            "grant_type": "refresh_token",
            "client_id": XAI_OAUTH_CLIENT_ID,
            "refresh_token": tokens.refresh_token,
        },
        timeout=30,
    )
    refreshed = _parse_token_response(response, tokens.token_endpoint, tokens.redirect_uri, started_at, tokens.refresh_token)
    save_tokens(refreshed)
    return refreshed


def get_valid_tokens() -> TokenSet:
    tokens = load_tokens()
    if tokens is None:
        raise RuntimeError("Not logged in. Run `uv run tomo login` first.")
    if tokens.expired:
        return refresh_tokens(tokens)
    return tokens


def login() -> TokenSet:
    discovery = discover_oauth()
    pkce = generate_pkce()
    state = secrets.token_hex(24)
    nonce = secrets.token_hex(24)
    port = _free_port(XAI_OAUTH_REDIRECT_PORT)
    redirect_uri = f"http://{XAI_OAUTH_REDIRECT_HOST}:{port}{XAI_OAUTH_REDIRECT_PATH}"
    authorize_url = build_authorize_url(redirect_uri, pkce.challenge, state, nonce)
    print(f"Opening browser for xAI login: {authorize_url}")
    webbrowser.open(authorize_url)
    code = wait_for_callback(port, state)
    tokens = exchange_code(discovery.token_endpoint, code, redirect_uri, pkce)
    save_tokens(tokens)
    return tokens


def _parse_token_response(
    response: httpx.Response,
    token_endpoint: str,
    redirect_uri: str,
    started_at: float,
    fallback_refresh_token: str = "",
) -> TokenSet:
    if response.status_code >= 400:
        raise RuntimeError(f"xAI token request failed HTTP {response.status_code}: {response.text}")
    payload = response.json()
    access_token = str(payload.get("access_token", "")).strip()
    refresh_token = str(payload.get("refresh_token", fallback_refresh_token)).strip()
    if not access_token:
        raise RuntimeError("xAI token response did not include access_token")
    if not refresh_token:
        raise RuntimeError("xAI token response did not include refresh_token")
    expires_in = payload.get("expires_in")
    expires_at = started_at + float(expires_in if isinstance(expires_in, (int, float)) else 3600)
    return TokenSet(
        access_token=access_token,
        refresh_token=refresh_token,
        token_endpoint=token_endpoint,
        redirect_uri=redirect_uri,
        expires_at=expires_at,
        token_type=str(payload.get("token_type", "Bearer") or "Bearer"),
    )
