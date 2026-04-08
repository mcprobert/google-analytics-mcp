"""Interactive script to register a Google account for GA4 MCP access.

Run from terminal:
    ga4-mcp-add-account
    ga4-mcp-add-account --client-secrets /path/to/client_secret.json

This opens a browser for Google login, then saves the refresh token
so the MCP server can access GA4 properties under that account.
"""

import argparse
import json
import sys
import urllib.request

from google_auth_oauthlib.flow import InstalledAppFlow

from ga4_mcp.auth import SCOPES, save_oauth_credentials
from ga4_mcp.default_client import CLIENT_CONFIG, get_default_client_config


def main():
    parser = argparse.ArgumentParser(description="Register a Google account for GA4 MCP access")
    parser.add_argument(
        "--client-secrets",
        required=False,
        default=None,
        help="Path to OAuth client secrets JSON file (from Google Cloud Console). "
             "If omitted, uses the built-in credentials.",
    )
    args = parser.parse_args()

    if args.client_secrets:
        flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, scopes=SCOPES)
    else:
        if not get_default_client_config():
            print(
                "ERROR: No OAuth client credentials available.\n"
                "Either pass --client-secrets or ensure the package has embedded credentials configured.",
                file=sys.stderr,
            )
            sys.exit(1)
        flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)

    print("Opening browser for Google login...")
    print("Grant access to Google Analytics when prompted.\n")

    creds = flow.run_local_server(port=0)

    # Get the user's email address
    userinfo_url = "https://www.googleapis.com/oauth2/v2/userinfo"
    req = urllib.request.Request(userinfo_url)
    req.add_header("Authorization", f"Bearer {creds.token}")
    response = urllib.request.urlopen(req)
    userinfo = json.loads(response.read().decode())
    email = userinfo.get("email")

    if not email:
        print("ERROR: Could not determine email address from login.", file=sys.stderr)
        sys.exit(1)

    # Extract client_id and client_secret from the flow
    client_config = flow.client_config
    # `creds.scopes` is the only scope-bearing attribute on google.oauth2.credentials.Credentials
    # in google-auth 2.40.0. InstalledAppFlow populates it from the token-exchange response's
    # `scope` field. Fall back to SCOPES if empty (very old responses).
    granted_scopes = list(creds.scopes) if creds.scopes else list(SCOPES)
    save_oauth_credentials(
        email=email,
        refresh_token=creds.refresh_token,
        client_id=client_config["client_id"],
        client_secret=client_config["client_secret"],
        granted_scopes=granted_scopes,
    )

    print(f"\nAccount registered: {email}")
    print("This account is now available in the MCP server via the 'account' parameter.")


if __name__ == "__main__":
    main()
