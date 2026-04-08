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

READ_SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/analytics.manage.users.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
EDIT_SCOPES = [
    "https://www.googleapis.com/auth/analytics.edit",
]
# New registrations request the full scope set. Existing refresh tokens granted only
# the read scopes continue to work for read tools; write tools gate on granted_scopes.
SCOPES = READ_SCOPES + EDIT_SCOPES
EDIT_SCOPE = EDIT_SCOPES[0]

# Pending OAuth flow state, guarded by _flow_lock
_pending_flow = None
_flow_lock = threading.Lock()


def _write_private_json(path: Path, payload: dict) -> None:
    """Atomically write JSON to a file with restrictive permissions (0o600).

    Writes to a temp sibling file first, then os.replace()s into place. os.replace
    is atomic on POSIX and atomic on Windows 10+ for same-volume renames, so
    readers (including save_oauth_credentials's cache-pop path) never observe a
    partially-written file. On failure, the original file is left intact and the
    temp file is cleaned up.
    """
    tmp_path = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        if os.name != "nt":
            os.chmod(str(tmp_path), 0o600)
        os.replace(str(tmp_path), str(path))
    except Exception:
        # Best-effort cleanup of the tmp file. FileNotFoundError is expected if
        # os.open raised before creating the file; other OSErrors (permission,
        # disk full on unlink itself) propagate as the original exception would.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _get_oauth_client_config() -> dict:
    """Load OAuth client config from env var, falling back to embedded defaults.

    Priority:
    1. GA4_MCP_OAUTH_CLIENT_SECRETS env var (path to JSON file)
    2. Embedded client credentials from default_client.py
    """
    path = os.getenv("GA4_MCP_OAUTH_CLIENT_SECRETS")
    if path:
        path = Path(path)
        if path.exists():
            data = json.loads(path.read_text())
            return data.get("installed") or data.get("web")

    # Fall back to embedded client credentials
    from ga4_mcp.default_client import get_default_client_config
    return get_default_client_config()


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
    """Load OAuth credentials for a registered account, refreshing if needed.

    Does NOT attempt to backfill granted_scopes for legacy (schema-v1) account files.
    After constructing Credentials with scopes=SCOPES, the post-refresh value of
    creds.scopes is not a reliable indicator of what Google actually granted — the
    refresh response's `scope` field is optional and may be absent. Trying to infer
    granted scopes from creds.scopes risks falsely reporting edit access on a
    read-only account. Legacy accounts therefore remain schema-v1 until the user
    re-runs `ga4-mcp-add-account` (or the add_account MCP tool), which writes the
    authoritative granted_scopes from the token-exchange response.
    """
    # dict.get() is atomic under CPython's GIL / per-dict lock — do NOT split
    # into `in` + subscript, that re-introduces a TOCTOU race with
    # save_oauth_credentials's cache pop when FastMCP runs sync tools on a
    # thread pool. TODO: coalesce concurrent refreshes — two cache misses on
    # the same email currently trigger two simultaneous token exchanges against
    # Google. Benign today (Google tolerates duplicate refresh_token grants)
    # but would break under any future refresh-token rotation scheme.
    cached = _credentials_cache.get(email)
    if cached is not None and cached.valid:
        return cached

    token_file = _safe_account_path(email)
    if not token_file.exists():
        raise ValueError(
            f"No stored credentials for '{email}'. "
            f"Use the add_account tool to register this account."
        )

    data = json.loads(token_file.read_text())
    # CRITICAL: do NOT pass scopes=SCOPES here.
    #
    # SCOPES includes analytics.edit (added in v3.2.0). google-auth's
    # reauth.refresh_grant sends `scope=<space-joined>` in the refresh POST body
    # whenever creds.scopes is truthy (google-auth 2.40.0, oauth2/reauth.py line
    # ~40 and oauth2/_client.py line 500). Per RFC 6749 §6 and Google's token
    # endpoint, requesting any scope NOT in the original grant is rejected with
    # `invalid_scope` — which means a legacy v3.1.0 account (granted only the
    # read scopes) would have every refresh fail on upgrade, breaking even
    # read-only tools.
    #
    # Pass the PERSISTED grant (schema-v2 accounts) or None (schema-v1 legacy
    # accounts). None omits the scope parameter entirely, so Google returns
    # whatever scopes the refresh token was originally authorized for.
    persisted_scopes = data.get("granted_scopes") or None
    creds = Credentials(
        token=None,
        refresh_token=data["refresh_token"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        scopes=persisted_scopes,
    )

    from google.auth.transport.requests import Request
    creds.refresh(Request())

    _credentials_cache[email] = creds
    return creds


def save_oauth_credentials(
    email: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    granted_scopes: list = None,
):
    """Save OAuth credentials for an account with restrictive file permissions.

    Args:
        granted_scopes: The scopes Google actually granted (from the token response `scope`
            field). When None/empty, the on-disk file omits `granted_scopes` entirely and
            the account is treated as legacy/read-only by `ensure_edit_scope`.
    """
    token_file = _safe_account_path(email)
    data = {
        "email": email,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
        "schema_version": 2,
    }
    if granted_scopes:
        data["granted_scopes"] = list(granted_scopes)
    _write_private_json(token_file, data)
    # Invalidate any in-memory cached credential for this email so the next call
    # rereads the JSON file and picks up the new refresh token / scope grant. Without
    # this, re-auth inside a running server process is a no-op until the cached token
    # expires naturally.
    _credentials_cache.pop(email, None)
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


def get_granted_scopes(email: str) -> list:
    """Read granted_scopes from an account's stored token file.

    Returns an empty list for legacy accounts (schema v1, no granted_scopes field) or
    accounts that do not exist. Does NOT raise — callers use the empty list to decide
    whether the account has any particular scope.
    """
    try:
        token_file = _safe_account_path(email)
    except ValueError:
        return []
    if not token_file.exists():
        return []
    try:
        data = json.loads(token_file.read_text())
    except Exception:
        return []
    return list(data.get("granted_scopes") or [])


def ensure_edit_scope(account: str = None):
    """Verify that an account has the analytics.edit scope.

    Returns None on success (account has the scope). Returns an error dict on failure
    that write tools should return directly to the caller — never raise.
    """
    if not account:
        return {
            "error": "Write tools require an OAuth account — service account / ADC cannot be scope-checked.",
            "required_scope": EDIT_SCOPE,
            "remediation": (
                "Pass account=\"user@example.com\" on the tool call. Register an OAuth "
                "account via the add_account MCP tool or run `ga4-mcp-add-account` in a terminal."
            ),
        }

    # Explicit corrupt-file detection BEFORE get_granted_scopes, which
    # silently swallows JSONDecodeError and returns [] — that would make
    # us report "legacy registration" for what is actually a file-
    # corruption problem, wasting audit time on the wrong remediation.
    # get_granted_scopes stays simple because list_accounts uses it for
    # display (returning [] is fine there; the user sees can_edit: False).
    try:
        token_file = _safe_account_path(account)
    except ValueError as e:
        return {
            "error": f"Invalid account identifier '{account}': {e}",
            "required_scope": EDIT_SCOPE,
            "account": account,
        }
    if token_file.exists():
        try:
            json.loads(token_file.read_text())
        except json.JSONDecodeError as e:
            return {
                "error": (
                    f"Credential file for '{account}' is corrupt or unreadable: {e}. "
                    f"Re-register via the add_account MCP tool."
                ),
                "required_scope": EDIT_SCOPE,
                "account": account,
                "exception_type": "JSONDecodeError",
            }
        except OSError as e:
            return {
                "error": f"Failed to read credential file for '{account}': {e}",
                "required_scope": EDIT_SCOPE,
                "account": account,
            }

    granted = get_granted_scopes(account)
    if not granted:
        # Either the file is missing, unreadable, or it's a legacy v1 file with no
        # granted_scopes field. We cannot prove edit access — force re-auth.
        return {
            "error": (
                f"Account '{account}' has no recorded scope grants (legacy registration "
                f"or unknown account). Cannot use write tools until the account is re-authorized."
            ),
            "required_scope": EDIT_SCOPE,
            "account": account,
            "granted_scopes": granted,
            "remediation": (
                "Re-authorize the account: call the add_account MCP tool and sign in with "
                "the SAME Google account. Google's include_granted_scopes flag means any "
                "scopes you previously granted are preserved. If you prefer the terminal, "
                "run `ga4-mcp-add-account` — in that case you must RESTART the MCP server "
                "afterwards so it picks up the new credentials (the in-process credential "
                "cache is per-process)."
            ),
        }
    if EDIT_SCOPE in granted:
        return None
    return {
        "error": "This tool requires GA4 edit permission, which has not been granted for this account.",
        "required_scope": EDIT_SCOPE,
        "account": account,
        "granted_scopes": granted,
        "remediation": (
            "Re-authorize the account: call the add_account MCP tool and sign in with "
            "the SAME Google account. When prompted by Google, grant the 'Edit Google "
            "Analytics' permission. Selecting a different account during consent does "
            "NOT merge scopes. If you prefer the terminal, run `ga4-mcp-add-account` — "
            "in that case you must RESTART the MCP server afterwards so it picks up "
            "the new credentials (the in-process credential cache is per-process)."
        ),
    }


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
        return {"error": "OAuth client credentials not available. "
                         "Either set GA4_MCP_OAUTH_CLIENT_SECRETS to a client secrets JSON file, "
                         "or ensure the package has embedded client credentials configured."}

    client_id = client_config["client_id"]
    client_secret = client_config["client_secret"]
    state = secrets.token_urlsafe(32)

    # Start local server on a random port
    host = "127.0.0.1"
    server = _OAuthServer((host, 0), _OAuthCallbackHandler, expected_state=state)
    port = server.server_address[1]
    redirect_uri = f"http://{host}:{port}"

    # Build auth URL. include_granted_scopes=true lets re-auth upgrade read-only accounts
    # to edit without losing previously granted scopes — provided the user signs in with
    # the SAME Google account.
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
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

        # Google's token response includes a `scope` field (space-separated) listing the
        # scopes actually granted. Fall back to the full requested SCOPES list only if
        # that field is missing — in which case we assume the user granted everything.
        granted_scopes = tokens.get("scope", "").split() or list(SCOPES)

        save_oauth_credentials(
            email=email,
            refresh_token=tokens["refresh_token"],
            client_id=flow["client_id"],
            client_secret=flow["client_secret"],
            granted_scopes=granted_scopes,
        )

        return {"email": email, "status": "success", "granted_scopes": granted_scopes}

    except Exception as e:
        if "error" not in str(type(e).__name__).lower():
            return {"error": f"Token exchange failed: {e}"}
        raise

    finally:
        server.shutdown()
        server.server_close()
        with _flow_lock:
            _pending_flow = None
