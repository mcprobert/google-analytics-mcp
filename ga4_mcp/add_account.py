"""Interactive script to register a Google account for GA4 MCP access.

Run from terminal:
    python -m ga4_mcp.add_account --client-secrets /path/to/client_secret.json

This opens a browser for Google login, then saves the refresh token
so the MCP server can access GA4 properties under that account.
"""

import argparse
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from ga4_mcp.auth import SCOPES, save_oauth_credentials


def main():
    parser = argparse.ArgumentParser(description="Register a Google account for GA4 MCP access")
    parser.add_argument(
        "--client-secrets",
        required=True,
        help="Path to OAuth client secrets JSON file (from Google Cloud Console)",
    )
    args = parser.parse_args()

    print("Opening browser for Google login...")
    print("Grant access to Google Analytics when prompted.\n")

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, scopes=SCOPES)
    creds = flow.run_local_server(port=0)

    # Get the user's email address
    import json
    import urllib.request

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
    save_oauth_credentials(
        email=email,
        refresh_token=creds.refresh_token,
        client_id=client_config["client_id"],
        client_secret=client_config["client_secret"],
    )

    print(f"\nAccount registered: {email}")
    print("This account is now available in the MCP server via the 'account' parameter.")


if __name__ == "__main__":
    main()
