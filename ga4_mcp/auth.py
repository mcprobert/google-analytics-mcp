"""Centralized credential management for multi-account GA4 access.

Supports two credential types:
- Service account / ADC: from GOOGLE_APPLICATION_CREDENTIALS or application default credentials
- OAuth accounts: stored refresh tokens from interactive login

Tokens are stored in ~/.config/ga4-mcp/accounts/{email}.json
"""

import html
import json
import os
import secrets
import sys
import threading
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from google.oauth2.credentials import Credentials

ACCOUNTS_DIR = Path.home() / ".config" / "ga4-mcp" / "accounts"

# In-memory cache of loaded credentials: {email: google.oauth2.credentials.Credentials}
_credentials_cache = {}

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/analytics.manage.users.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

# Pending OAuth flow state, guarded by _flow_lock
_pending_flow = None
_flow_lock = threading.Lock()


def _write_private_json(path: Path, payload: dict) -> None:
    """Write JSON to a file with restrictive permissions (0o600)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    if os.name != "nt":
        os.chmod(str(path), 0o600)


def _get_oauth_client_config() -> dict:
    """Load OAuth client config from the configured path."""
    path = os.getenv("GA4_MCP_OAUTH_CLIENT_SECRETS")
    if not path:
        return None
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data.get("installed") or data.get("web")


def get_accounts_dir() -> Path:
    """Return the accounts directory, creating it with restrictive permissions."""
    ACCOUNTS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        ACCOUNTS_DIR.chmod(0o700)
    return ACCOUNTS_DIR


def _safe_account_path(email: str) -> Path:
    """Construct a safe file path for an account email, preventing path traversal."""
    if not email:
        raise ValueError("Account email is required.")
    base = get_accounts_dir().resolve()
    candidate = (base / f"{email}.json").resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError:
        raise ValueError("Invalid account identifier.")
    return candidate


def list_registered_accounts() -> list[dict]:
    """List all registered OAuth accounts."""
    accounts_dir = get_accounts_dir()
    accounts = []
    for token_file in sorted(accounts_dir.glob("*.json")):
        try:
            data = json.loads(token_file.read_text())
            accounts.append({
                "email": data.get("email", token_file.stem),
                "type": "oauth",
            })
        except Exception:
            continue
    return accounts


def has_default_credentials() -> bool:
    """Check if application default credentials are available."""
    try:
        from google.auth import default as auth_default
        auth_default()
        return True
    except Exception:
        return False


def get_oauth_credentials(email: str) -> Credentials:
    """Load OAuth credentials for a registered account, refreshing if needed."""
    if email in _credentials_cache:
        creds = _credentials_cache[email]
        if creds.valid:
            return creds

    token_file = _safe_account_path(email)
    if not token_file.exists():
        raise ValueError(
            f"No stored credentials for '{email}'. "
            f"Use the add_account tool to register this account."
        )

    data = json.loads(token_file.read_text())
    creds = Credentials(
        token=None,
        refresh_token=data["refresh_token"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        scopes=SCOPES,
    )

    from google.auth.transport.requests import Request
    creds.refresh(Request())

    _credentials_cache[email] = creds
    return creds


def save_oauth_credentials(email: str, refresh_token: str, client_id: str, client_secret: str):
    """Save OAuth credentials for an account with restrictive file permissions."""
    token_file = _safe_account_path(email)
    data = {
        "email": email,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    _write_private_json(token_file, data)
    print(f"Credentials saved to {token_file}", file=sys.stderr)


def resolve_credentials(account: str = None):
    """Resolve credentials from an account parameter.

    Returns:
        google credentials object, or None to use default (service account / ADC).
    """
    if not account:
        return None
    try:
        return get_oauth_credentials(account)
    except ValueError:
        raise


def remove_account(email: str) -> bool:
    """Remove a registered OAuth account."""
    token_file = _safe_account_path(email)
    if token_file.exists():
        token_file.unlink()
        _credentials_cache.pop(email, None)
        return True
    return False


# --- Two-phase interactive OAuth flow with per-flow state ---

class _OAuthServer(HTTPServer):
    """HTTP server that carries per-flow OAuth state."""

    def __init__(self, addr, handler_class, *, expected_state: str):
        super().__init__(addr, handler_class)
        self.expected_state = expected_state
        self.done = threading.Event()
        self.result = {}  # {"auth_code": ...} or {"error": ...}


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback code."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        server = self.server  # type: _OAuthServer

        # Validate state parameter
        received_state = params.get("state", [None])[0]
        if received_state != server.expected_state:
            server.result = {"error": "Invalid OAuth state parameter."}
            self._send_html(400, "<h2>Authentication failed: invalid state</h2>")
            server.done.set()
            return

        if "code" in params:
            server.result = {"auth_code": params["code"][0]}
            self._send_html(200,
                "<h2>Authentication successful!</h2>"
                "<p>You can close this window and return to Claude.</p>")
        elif "error" in params:
            error_text = html.escape(params["error"][0], quote=True)
            server.result = {"error": params["error"][0]}
            self._send_html(400, f"<h2>Authentication failed: {error_text}</h2>")
        else:
            server.result = {"error": "No code or error in callback."}
            self._send_html(400, "<h2>Unexpected callback</h2>")

        server.done.set()

    def _send_html(self, code: int, body: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body>{body}</body></html>".encode("utf-8"))

    def log_message(self, format, *args):
        pass


def start_oauth_flow() -> dict:
    """Phase 1: Start OAuth flow — returns auth URL for the user to visit.

    Starts a background HTTP server waiting for the OAuth callback.
    Call complete_oauth_flow() after the user has visited the URL.
    """
    global _pending_flow

    with _flow_lock:
        if _pending_flow is not None:
            return {"error": "An OAuth flow is already in progress. "
                             "Call complete_account_login() first, or wait for it to time out."}

    client_config = _get_oauth_client_config()
    if not client_config:
        return {"error": "GA4_MCP_OAUTH_CLIENT_SECRETS environment variable not set or file not found. "
                         "Set it to the path of your OAuth client secrets JSON file."}

    client_id = client_config["client_id"]
    client_secret = client_config["client_secret"]
    state = secrets.token_urlsafe(32)

    # Start local server on a random port
    host = "127.0.0.1"
    server = _OAuthServer((host, 0), _OAuthCallbackHandler, expected_state=state)
    port = server.server_address[1]
    redirect_uri = f"http://{host}:{port}"

    # Build auth URL
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(auth_params)}"

    # Run server in background thread
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    # Store pending flow state
    with _flow_lock:
        _pending_flow = {
            "server": server,
            "server_thread": server_thread,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }

    return {"auth_url": auth_url}


def complete_oauth_flow(timeout_seconds: int = 120) -> dict:
    """Phase 2: Wait for OAuth callback and exchange code for tokens.

    Call this after the user has been shown the auth URL from start_oauth_flow().
    Blocks until the user completes authentication or timeout.
    """
    global _pending_flow

    with _flow_lock:
        if not _pending_flow:
            return {"error": "No pending OAuth flow. Call add_account() first."}
        flow = _pending_flow

    server = flow["server"]  # type: _OAuthServer

    try:
        # Wait for callback via event
        server.done.wait(timeout=timeout_seconds)

        result = server.result

        if "error" in result:
            return {"error": f"OAuth failed: {result['error']}"}

        if "auth_code" not in result:
            return {"error": "OAuth timed out. The user did not complete authentication in time."}

        # Exchange auth code for tokens
        token_data = {
            "code": result["auth_code"],
            "client_id": flow["client_id"],
            "client_secret": flow["client_secret"],
            "redirect_uri": flow["redirect_uri"],
            "grant_type": "authorization_code",
        }
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode(token_data).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            tokens = json.loads(response.read().decode())

        # Get user email
        userinfo_req = urllib.request.Request("https://www.googleapis.com/oauth2/v2/userinfo")
        userinfo_req.add_header("Authorization", f"Bearer {tokens['access_token']}")
        with urllib.request.urlopen(userinfo_req, timeout=10) as userinfo_response:
            userinfo = json.loads(userinfo_response.read().decode())
        email = userinfo.get("email")

        if not email:
            return {"error": "Could not determine email address from login."}

        save_oauth_credentials(
            email=email,
            refresh_token=tokens["refresh_token"],
            client_id=flow["client_id"],
            client_secret=flow["client_secret"],
        )

        return {"email": email, "status": "success"}

    except Exception as e:
        if "error" not in str(type(e).__name__).lower():
            return {"error": f"Token exchange failed: {e}"}
        raise

    finally:
        server.shutdown()
        server.server_close()
        with _flow_lock:
            _pending_flow = None
