#!/usr/bin/env python3
"""
Grant the GA4 MCP service account Viewer access to all GA4 accounts
accessible by the authenticated user.

Usage:
    python scripts/grant_access_all_accounts.py

This will open a browser for Google OAuth login, then iterate through
all GA accounts and add the service account as a Viewer.
"""

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from google.analytics.admin_v1alpha import AnalyticsAdminServiceClient
from google.analytics.admin_v1alpha.types import (
    AccessBinding,
    ListAccountsRequest,
    CreateAccessBindingRequest,
    ListAccessBindingsRequest,
)

ROOT = Path(__file__).resolve().parents[1]

OAUTH_CLIENT_SECRET = ROOT / "docs" / "client_secret_355080528088-n58clrv4038i8m9mvq8ot4f6co769m3m.apps.googleusercontent.com.json"
SERVICE_ACCOUNT_EMAIL = "ga4-mcp-reader@ga-mcp-server-491313.iam.gserviceaccount.com"

SCOPES = [
    "https://www.googleapis.com/auth/analytics.manage.users",
    "https://www.googleapis.com/auth/analytics.readonly",
]


def authenticate():
    """Run OAuth flow and return credentials."""
    flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH_CLIENT_SECRET), scopes=SCOPES)
    creds = flow.run_local_server(port=0)
    return creds


def main():
    print("Authenticating via browser...")
    creds = authenticate()

    client = AnalyticsAdminServiceClient(credentials=creds)

    print("\nListing all GA accounts you have access to...\n")
    accounts = list(client.list_accounts(request=ListAccountsRequest()))

    if not accounts:
        print("No accounts found. Check that your Google account has GA access.")
        sys.exit(1)

    print(f"Found {len(accounts)} account(s):\n")
    for account in accounts:
        print(f"  {account.display_name} ({account.name})")

    print(f"\nGranting Viewer access to: {SERVICE_ACCOUNT_EMAIL}\n")

    granted = 0
    skipped = 0
    failed = 0

    for account in accounts:
        # Check if service account already has access
        try:
            existing = list(client.list_access_bindings(
                request=ListAccessBindingsRequest(parent=account.name)
            ))
            already_has_access = any(
                b.user == SERVICE_ACCOUNT_EMAIL for b in existing
            )
            if already_has_access:
                print(f"  SKIP  {account.display_name} - already has access")
                skipped += 1
                continue
        except Exception as e:
            # If we can't list bindings, try to create anyway
            pass

        # Grant access
        try:
            binding = AccessBinding(
                user=SERVICE_ACCOUNT_EMAIL,
                roles=["predefinedRoles/viewer"],
            )
            client.create_access_binding(
                request=CreateAccessBindingRequest(
                    parent=account.name,
                    access_binding=binding,
                )
            )
            print(f"  OK    {account.display_name}")
            granted += 1
        except Exception as e:
            error_msg = str(e)
            if "already exists" in error_msg.lower() or "ALREADY_EXISTS" in error_msg:
                print(f"  SKIP  {account.display_name} - already has access")
                skipped += 1
            else:
                print(f"  FAIL  {account.display_name} - {error_msg[:120]}")
                failed += 1

    print(f"\nDone: {granted} granted, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
