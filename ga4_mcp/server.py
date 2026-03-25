# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys
from .coordinator import mcp
from .tools import metadata, reporting
from .auth import list_registered_accounts

def main():
    """
    Main entry point for the MCP server.

    This function performs the following steps:
    1. Validates required environment variables.
    2. If a default GA4_PROPERTY_ID is set, fetches and caches its schema.
    3. Discovers registered OAuth accounts.
    4. Starts the server and listens for requests.
    """
    print("Starting GA4 MCP server...", file=sys.stderr)

    # 1. Validate credentials (service account is optional if OAuth accounts exist)
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    property_id = os.getenv("GA4_PROPERTY_ID")

    if credentials_path and not os.path.exists(credentials_path):
        print(f"WARNING: Credentials file not found at '{credentials_path}'.", file=sys.stderr)

    # 2. Set default property ID and pre-cache its schema if provided
    if property_id and not property_id.strip().isdigit():
        print(f"WARNING: GA4_PROPERTY_ID '{property_id}' is not numeric. "
              "Use the numeric property ID, not a Measurement ID (G-XXXX).", file=sys.stderr)
        property_id = None

    metadata.DEFAULT_PROPERTY_ID = property_id

    if property_id:
        print(f"Default property: {property_id}", file=sys.stderr)
        try:
            metadata._get_schema(property_id)
        except Exception as e:
            print(f"WARNING: Could not fetch schema for default property '{property_id}': {e}", file=sys.stderr)
            print("Schema will be fetched on first tool call.", file=sys.stderr)
    else:
        print("No default GA4_PROPERTY_ID set. Property ID must be provided on each tool call.", file=sys.stderr)

    # 3. Discover registered OAuth accounts
    oauth_accounts = list_registered_accounts()
    if oauth_accounts:
        print(f"Registered OAuth accounts: {', '.join(a['email'] for a in oauth_accounts)}", file=sys.stderr)
    else:
        print("No OAuth accounts registered. Use 'python -m ga4_mcp.add_account' to add one.", file=sys.stderr)

    # 4. Run the server
    print("GA4 MCP server ready.", file=sys.stderr)
    mcp.run(transport="stdio")
