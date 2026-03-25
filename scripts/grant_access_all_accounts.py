#!/usr/bin/env python3
"""
Grant a service account Viewer access to all GA4 accounts
accessible by the authenticated user.

Usage:
    python scripts/grant_access_all_accounts.py \
        --client-secrets /path/to/oauth-client-secret.json \
        --service-account-email sa@project.iam.gserviceaccount.com

This will open a browser for Google OAuth login, then iterate through
all GA accounts and add the service account as a Viewer.
"""

import argparse
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from google.analytics.admin_v1alpha import AnalyticsAdminServiceClient
from google.analytics.admin_v1alpha.types import (
    AccessBinding,
    ListAccountsRequest,
    CreateAccessBindingRequest,
    ListAccessBindingsRequest,
)

SCOPES = [
    "https://www.googleapis.com/auth/analytics.manage.users",
    "https://www.googleapis.com/auth/analytics.readonly",
    "openid",
]


def authenticate(client_secrets_path: str):
    """Run OAuth flow and return credentials."""
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, scopes=SCOPES)
    creds = flow.run_local_server(port=0)
    return creds


def main():
    parser = argparse.ArgumentParser(
        description="Grant a service account Viewer access to all GA4 accounts"
    )
    parser.add_argument(
        "--client-secrets",
        required=True,
        help="Path to OAuth client secrets JSON file",
    )
    parser.add_argument(
        "--service-account-email",
        required=True,
        help="Service account email to grant access to (e.g. sa@project.iam.gserviceaccount.com)",
    )
    args = parser.parse_args()

    print("Authenticating via browser...")
    creds = authenticate(args.client_secrets)

    client = AnalyticsAdminServiceClient(credentials=creds)

    print("\nListing all GA accounts you have access to...\n")
    accounts = list(client.list_accounts(request=ListAccountsRequest()))

    if not accounts:
        print("No accounts found. Check that your Google account has GA access.")
        sys.exit(1)

    print(f"Found {len(accounts)} account(s):\n")
    for account in accounts:
        print(f"  {account.display_name} ({account.name})")

    print(f"\nGranting Viewer access to: {args.service_account_email}\n")

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
                b.user == args.service_account_email for b in existing
            )
            if already_has_access:
                print(f"  SKIP  {account.display_name} - already has access")
                skipped += 1
                continue
        except Exception:
            pass

        # Grant access
        try:
            binding = AccessBinding(
                user=args.service_account_email,
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
