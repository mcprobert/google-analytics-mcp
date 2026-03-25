"""Centralized credential management for multi-account GA4 access.

Supports two credential types:
- Service account: from GOOGLE_APPLICATION_CREDENTIALS env var (the default)
- OAuth accounts: stored refresh tokens from interactive login

Tokens are stored in ~/.config/ga4-mcp/accounts/{email}.json
"""

import json
import os
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

# Pending OAuth flow state
_pending_flow = None


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
    """Return the accounts directory, creating it if needed."""
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    return ACCOUNTS_DIR


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


def get_oauth_credentials(email: str) -> Credentials:
    """Load OAuth credentials for a registered account, refreshing if needed."""
    if email in _credentials_cache:
        creds = _credentials_cache[email]
        if creds.valid:
            return creds

    token_file = get_accounts_dir() / f"{email}.json"
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
    """Save OAuth credentials for an account."""
    token_file = get_accounts_dir() / f"{email}.json"
    data = {
        "email": email,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    token_file.write_text(json.dumps(data, indent=2))
    print(f"Credentials saved to {token_file}", file=sys.stderr)


def resolve_credentials(account: str = None):
    """Resolve credentials from an account parameter.

    Returns:
        google credentials object, or None to use default (service account).
    """
    if not account:
        return None
    return get_oauth_credentials(account)


def remove_account(email: str) -> bool:
    """Remove a registered OAuth account."""
    token_file = get_accounts_dir() / f"{email}.json"
    if token_file.exists():
        token_file.unlink()
        _credentials_cache.pop(email, None)
        return True
    return False


# --- Two-phase interactive OAuth flow ---

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback code."""

    auth_code = None
    error = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Authentication successful!</h2>"
                             b"<p>You can close this window and return to Claude.</p></body></html>")
        elif "error" in params:
            _OAuthCallbackHandler.error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body><h2>Authentication failed: {params['error'][0]}</h2></body></html>".encode())
        else:
            self.send_response(400)
            self.end_headers()

        threading.Thread(target=self.server.shutdown).start()

    def log_message(self, format, *args):
        pass


def start_oauth_flow() -> dict:
    """Phase 1: Start OAuth flow — returns auth URL for the user to visit.

    Starts a background HTTP server waiting for the OAuth callback.
    Call complete_oauth_flow() after the user has visited the URL.
    """
    global _pending_flow

    client_config = _get_oauth_client_config()
    if not client_config:
        return {"error": "GA4_MCP_OAUTH_CLIENT_SECRETS environment variable not set or file not found. "
                         "Set it to the path of your OAuth client secrets JSON file."}

    client_id = client_config["client_id"]
    client_secret = client_config["client_secret"]

    # Start local server
    server = HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}"

    # Reset handler state
    _OAuthCallbackHandler.auth_code = None
    _OAuthCallbackHandler.error = None

    # Build auth URL
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(auth_params)}"

    # Run server in background thread
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    # Store pending flow state
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

    if not _pending_flow:
        return {"error": "No pending OAuth flow. Call start_oauth_flow() first."}

    flow = _pending_flow
    server = flow["server"]
    server_thread = flow["server_thread"]

    # Wait for callback
    server_thread.join(timeout=timeout_seconds)

    # Clean up
    server.shutdown()
    _pending_flow = None

    if _OAuthCallbackHandler.error:
        return {"error": f"OAuth failed: {_OAuthCallbackHandler.error}"}

    if not _OAuthCallbackHandler.auth_code:
        return {"error": "OAuth timed out. The user did not complete authentication in time."}

    # Exchange auth code for tokens
    try:
        token_data = {
            "code": _OAuthCallbackHandler.auth_code,
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
        response = urllib.request.urlopen(req)
        tokens = json.loads(response.read().decode())

        # Get user email
        userinfo_req = urllib.request.Request("https://www.googleapis.com/oauth2/v2/userinfo")
        userinfo_req.add_header("Authorization", f"Bearer {tokens['access_token']}")
        userinfo_response = urllib.request.urlopen(userinfo_req)
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
        return {"error": f"Token exchange failed: {e}"}
