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

"""Tools for fetching and exploring GA4 property metadata."""

import sys
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from ga4_mcp.coordinator import mcp
from ga4_mcp.auth import (
    resolve_credentials, list_registered_accounts, has_default_credentials,
    start_oauth_flow, complete_oauth_flow,
    remove_account as _remove_account,
)

# Per-property schema cache: {(property_id, account): schema_dict}
SCHEMA_CACHE = {}

# Default property ID from environment (set on startup, can be None)
DEFAULT_PROPERTY_ID = None


def _resolve_property_id(property_id: str = None) -> str:
    """Resolve property ID from parameter, falling back to the default.

    Returns None if no ID available. Raises ValueError if ID is non-numeric.
    """
    pid = str(property_id or DEFAULT_PROPERTY_ID or "").strip()
    if not pid:
        return None
    if not pid.isdigit():
        raise ValueError(
            f"Invalid property_id '{pid}'. Use the numeric GA4 property ID, "
            f"not a Measurement ID like 'G-XXXX'."
        )
    return pid


def _get_schema(property_id: str, account: str = None) -> dict:
    """Get schema for a property, fetching and caching if needed."""
    cache_key = (property_id, account or "__default__")
    if cache_key not in SCHEMA_CACHE:
        print(f"Fetching schema for property '{property_id}'...", file=sys.stderr)
        SCHEMA_CACHE[cache_key] = _fetch_schema(property_id, account)
        print(f"Schema for property '{property_id}' loaded successfully.", file=sys.stderr)
    return SCHEMA_CACHE[cache_key]


def _fetch_schema(property_id: str, account: str = None) -> dict:
    """Fetch the full schema (dimensions and metrics) for a GA4 property."""
    creds = resolve_credentials(account)
    client = BetaAnalyticsDataClient(credentials=creds) if creds else BetaAnalyticsDataClient()
    request = {"name": f"properties/{property_id}/metadata"}
    metadata = client.get_metadata(request=request)

    schema = {"dimensions": {}, "metrics": {}}

    for dim in metadata.dimensions:
        schema["dimensions"][dim.api_name] = {
            "ui_name": dim.ui_name,
            "description": dim.description,
            "category": dim.category,
            "custom_definition": dim.custom_definition,
        }

    for met in metadata.metrics:
        schema["metrics"][met.api_name] = {
            "ui_name": met.ui_name,
            "description": met.description,
            "category": met.category,
            "type": met.type_.name,
        }
    return schema


def _resolve_pid_or_error(property_id: str = None) -> tuple:
    """Resolve property ID, returning (pid, None) or (None, error_dict)."""
    try:
        pid = _resolve_property_id(property_id)
    except ValueError as e:
        return None, {"error": str(e)}
    if not pid:
        return None, {"error": "No property_id provided and no default GA4_PROPERTY_ID configured."}
    return pid, None


@mcp.tool()
def list_accounts():
    """
    List all available accounts that can be used with the 'account' parameter.
    Returns default credentials (if available) and any registered OAuth user accounts.
    Register new accounts using the add_account tool or by running: ga4-mcp-add-account
    """
    accounts = []
    if has_default_credentials():
        accounts.append({"email": "default credentials", "type": "application_default"})
    accounts.extend(list_registered_accounts())
    return {"accounts": accounts, "total": len(accounts)}


@mcp.tool()
def add_account():
    """
    Step 1 of 2: Start registering a new Google account for GA4 access.

    Returns an authentication URL. You MUST present this URL to the user and ask
    them to click it to sign in with their Google account in the browser.

    After showing the URL, immediately call complete_account_login() which will
    wait for the user to finish authenticating.
    """
    result = start_oauth_flow()
    if "error" in result:
        return result
    return {
        "auth_url": result["auth_url"],
        "instructions": "Show this URL to the user and ask them to click it to sign in. "
                        "Then call complete_account_login() to finish registration.",
    }


@mcp.tool()
def complete_account_login():
    """
    Step 2 of 2: Complete the account registration after the user has clicked
    the authentication URL from add_account().

    This tool waits up to 2 minutes for the user to complete the Google sign-in
    in their browser. Once complete, the account is registered and available via
    the 'account' parameter on all other tools.
    """
    result = complete_oauth_flow(timeout_seconds=120)
    if "error" in result:
        return result
    return {
        "status": "success",
        "email": result["email"],
        "message": f"Account '{result['email']}' registered successfully. "
                   f"You can now use account='{result['email']}' on any tool.",
    }


@mcp.tool()
def remove_registered_account(email: str):
    """
    Remove a previously registered OAuth account.

    Args:
        email: The email address of the account to remove.
    """
    try:
        if _remove_account(email):
            return {"status": "success", "message": f"Account '{email}' removed."}
    except ValueError as e:
        return {"error": str(e)}
    return {"error": f"No registered account found for '{email}'."}


@mcp.tool()
def list_properties(account: str = None):
    """
    List all GA4 properties accessible by the given account.
    Returns account names and property IDs that can be used with other tools.

    Args:
        account: (Optional) Email of a registered OAuth account. If omitted, uses the
                 default credentials. Use list_accounts() to see available accounts.
    """
    try:
        from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
        from google.analytics.admin_v1beta.types import ListAccountsRequest, ListPropertiesRequest
    except ImportError:
        return {"error": "google-analytics-admin package not installed. Run: pip install google-analytics-admin"}

    try:
        creds = resolve_credentials(account)
        client = AnalyticsAdminServiceClient(credentials=creds) if creds else AnalyticsAdminServiceClient()
        ga_accounts = list(client.list_accounts(request=ListAccountsRequest()))

        result = []
        for ga_account in ga_accounts:
            request = ListPropertiesRequest(filter=f"parent:{ga_account.name}")
            for prop in client.list_properties(request=request):
                prop_id = prop.name.split("/")[-1]
                result.append({
                    "property_id": prop_id,
                    "property_name": prop.display_name,
                    "account_name": ga_account.display_name,
                })

        return {
            "properties": result,
            "default_property_id": DEFAULT_PROPERTY_ID,
            "using_account": account or "default credentials",
            "total": len(result),
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Failed to list properties: {e}"}


@mcp.tool()
def search_schema(keyword: str, property_id: str = None, account: str = None):
    """
    Searches for a keyword across all available dimensions and metrics for a property.
    Returns a short, ranked list of the most relevant fields. This is the most
    efficient way to discover dimensions and metrics for a specific query.

    Args:
        keyword: One or more keywords to search for (e.g., "user", "campaign revenue").
        property_id: (Optional) GA4 property ID (numeric). Defaults to the configured property.
        account: (Optional) Email of a registered OAuth account. Omit for default credentials.
    """
    pid, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    try:
        schema = _get_schema(pid, account)
    except Exception as e:
        return {"error": f"Failed to load schema for property '{pid}': {e}"}

    scores = {}
    search_terms = keyword.lower().split()

    for name, info in schema["dimensions"].items():
        score = 0
        for term in search_terms:
            if term in name.lower(): score += 10
            if term in info.get("ui_name", "").lower(): score += 5
            if term in info.get("description", "").lower(): score += 2
            if term in info.get("category", "").lower(): score += 1
        if score > 0:
            scores[f"DIMENSION: {name}"] = score

    for name, info in schema["metrics"].items():
        score = 0
        for term in search_terms:
            if term in name.lower(): score += 10
            if term in info.get("ui_name", "").lower(): score += 5
            if term in info.get("description", "").lower(): score += 2
            if term in info.get("category", "").lower(): score += 1
        if score > 0:
            scores[f"METRIC: {name}"] = score

    if not scores:
        return {"message": f"No dimensions or metrics found matching '{keyword}'."}

    sorted_results = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return {"top_results": dict(sorted_results[:10])}


@mcp.tool()
def get_property_schema(property_id: str = None, account: str = None):
    """
    Returns the complete schema for a GA4 property, including all
    available dimensions and metrics (standard and custom). Warning: This can be
    a very large object (10k+ tokens). Use search_schema for most discovery tasks.

    Args:
        property_id: (Optional) GA4 property ID (numeric). Defaults to the configured property.
        account: (Optional) Email of a registered OAuth account. Omit for default credentials.
    """
    pid, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    try:
        return _get_schema(pid, account)
    except Exception as e:
        return {"error": f"Failed to load schema for property '{pid}': {e}"}


@mcp.tool()
def list_dimension_categories(property_id: str = None, account: str = None):
    """
    List all available GA4 dimension categories based on a property's schema.
    This is a low-cost way to begin exploring the schema.

    Args:
        property_id: (Optional) GA4 property ID (numeric). Defaults to the configured property.
        account: (Optional) Email of a registered OAuth account. Omit for default credentials.
    """
    pid, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    try:
        schema = _get_schema(pid, account)
    except Exception as e:
        return {"error": f"Failed to load schema for property '{pid}': {e}"}

    categories = {}
    for dim_info in schema["dimensions"].values():
        category = dim_info.get("category", "Uncategorized")
        if category not in categories:
            categories[category] = 0
        categories[category] += 1

    return {"dimension_categories": categories}

@mcp.tool()
def list_metric_categories(property_id: str = None, account: str = None):
    """
    List all available GA4 metric categories based on a property's schema.
    This is a low-cost way to begin exploring the schema.

    Args:
        property_id: (Optional) GA4 property ID (numeric). Defaults to the configured property.
        account: (Optional) Email of a registered OAuth account. Omit for default credentials.
    """
    pid, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    try:
        schema = _get_schema(pid, account)
    except Exception as e:
        return {"error": f"Failed to load schema for property '{pid}': {e}"}

    categories = {}
    for met_info in schema["metrics"].values():
        category = met_info.get("category", "Uncategorized")
        if category not in categories:
            categories[category] = 0
        categories[category] += 1

    return {"metric_categories": categories}

@mcp.tool()
def get_dimensions_by_category(category: str, property_id: str = None, account: str = None):
    """
    Get all dimensions in a specific category with their descriptions.

    Args:
        category: The category name to retrieve dimensions for.
        property_id: (Optional) GA4 property ID (numeric). Defaults to the configured property.
        account: (Optional) Email of a registered OAuth account. Omit for default credentials.
    """
    pid, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    try:
        schema = _get_schema(pid, account)
    except Exception as e:
        return {"error": f"Failed to load schema for property '{pid}': {e}"}

    dimensions_in_category = {}
    for name, dim_info in schema["dimensions"].items():
        if dim_info.get("category", "Uncategorized").lower() == category.lower():
            dimensions_in_category[name] = dim_info["description"]

    if not dimensions_in_category:
        return {"error": f"Category '{category}' not found or has no dimensions."}

    return dimensions_in_category

@mcp.tool()
def get_metrics_by_category(category: str, property_id: str = None, account: str = None):
    """
    Get all metrics in a specific category with their descriptions.

    Args:
        category: The category name to retrieve metrics for.
        property_id: (Optional) GA4 property ID (numeric). Defaults to the configured property.
        account: (Optional) Email of a registered OAuth account. Omit for default credentials.
    """
    pid, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    try:
        schema = _get_schema(pid, account)
    except Exception as e:
        return {"error": f"Failed to load schema for property '{pid}': {e}"}

    metrics_in_category = {}
    for name, met_info in schema["metrics"].items():
        if met_info.get("category", "Uncategorized").lower() == category.lower():
            metrics_in_category[name] = met_info["description"]

    if not metrics_in_category:
        return {"error": f"Category '{category}' not found or has no metrics."}

    return metrics_in_category
