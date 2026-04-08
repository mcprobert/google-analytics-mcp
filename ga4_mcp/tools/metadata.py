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
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Metric, OrderBy, RunReportRequest,
)
from ga4_mcp.coordinator import mcp
from ga4_mcp.auth import (
    resolve_credentials, list_registered_accounts, has_default_credentials,
    start_oauth_flow, complete_oauth_flow,
    remove_account as _remove_account,
    get_granted_scopes, EDIT_SCOPE,
    _safe_account_path,
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

    Each OAuth account entry includes:
    - ``granted_scopes``: scopes Google actually granted at registration (empty for legacy accounts)
    - ``can_edit``: True when the account has the analytics.edit scope and can use write tools
    """
    accounts = []
    if has_default_credentials():
        # Service accounts / ADC cannot be scope-checked against analytics.edit.
        accounts.append({
            "email": "default credentials",
            "type": "application_default",
            "granted_scopes": [],
            "can_edit": False,
        })
    for entry in list_registered_accounts():
        email = entry.get("email")
        granted = get_granted_scopes(email) if email else []
        entry["granted_scopes"] = granted
        entry["can_edit"] = EDIT_SCOPE in granted
        accounts.append(entry)
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
def list_properties(account: str = None, name_contains: str = None):
    """
    List all GA4 properties accessible by the given account.
    Returns account names and property IDs that can be used with other tools.

    Args:
        account: (Optional) Email of a registered OAuth account. If omitted, uses the
                 default credentials. Use list_accounts() to see available accounts.
        name_contains: (Optional) Case-insensitive substring filter on property display
                       name. Useful for accounts with many properties.
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

        needle = name_contains.lower() if name_contains else None
        result = []
        for ga_account in ga_accounts:
            request = ListPropertiesRequest(filter=f"parent:{ga_account.name}")
            for prop in client.list_properties(request=request):
                if needle and needle not in (prop.display_name or "").lower():
                    continue
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
            "name_contains": name_contains,
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


@mcp.tool()
def ga4_health_check(property_id: str = None, account: str = None):
    """One-shot diagnostic for the start of an audit.

    Runs a handful of cheap read-only calls (no writes) and returns a summary of the
    property's measurement health — data stream count, whether events have been
    received recently, whether key events are configured, and whether the caller has
    the analytics.edit scope needed to fix things.

    **Tri-state status**: the ``status`` field takes one of three values:

    - ``"success"``          — all three probes succeeded, every field is trustworthy
    - ``"partial_success"``  — some probes failed; probe-dependent fields will be
      ``None`` to indicate "unknown" (not "measured zero")
    - No ``status`` field + ``error`` key — all probes failed; the response is an
      error dict with ``probes`` and ``warnings`` included for diagnostic context

    **Nullable scalar fields**: ``has_data``, ``has_key_events_configured``,
    ``key_events_configured_count``, ``has_key_event_activity_30d``, and
    ``data_stream_count`` are ``None`` when the underlying probe failed. This
    distinguishes "the probe ran and measured zero" from "we couldn't measure
    because of an auth/access failure". Callers that need definitive booleans
    should check ``status == "success"`` OR inspect the ``probes`` sub-dict.

    **Nullable collection fields**: ``data_streams`` (list) is ``None`` when the
    data-streams probe failed, matching ``data_stream_count``. An empty list
    ``[]`` means "probe succeeded, property has zero streams" — a real
    measured-zero, distinct from ``None`` which means "unknown". Callers MUST
    check ``probes.data_streams_ok`` before interpreting either field.

    **``last_event_received`` ambiguity**: this field is ``None`` in two
    distinct cases — (a) the Data API probe failed, or (b) the probe succeeded
    and no events were recorded in the selected period. Callers should consult
    ``probes.data_api_ok`` to disambiguate before drawing conclusions about
    tracking health.

    ``can_edit`` is always a definitive boolean — it reads stored OAuth scopes
    and does not depend on any probe.

    Partial-progress counts on probe failure are preserved in the warning
    label rather than in the response fields, e.g.
    ``list_data_streams_failed_after_3_streams: ...`` — use the warning text
    for diagnostic context, not the nulled fields.

    Args:
        property_id: (Optional) Numeric GA4 property ID. Defaults to the configured property.
        account: (Optional) Registered OAuth account email. Omit for default credentials.
    """
    pid, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    # --- Early account validation ---------------------------------------------------
    # Fail fast with a clear error for typo'd / unknown account names. We use
    # _safe_account_path + exists() directly instead of round-tripping through
    # resolve_credentials() because:
    #   1. It skips an unnecessary token refresh just to validate the account name.
    #   2. json.JSONDecodeError is a SUBCLASS of ValueError — a ValueError catch
    #      around resolve_credentials() would misclassify a corrupt token file as
    #      "account not found". Using exists() sidesteps the ambiguity entirely.
    if account:
        try:
            _candidate_path = _safe_account_path(account)
        except ValueError as e:
            return {
                "error": f"Invalid account identifier '{account}': {e}",
                "property_id": pid,
                "account": account,
            }
        if not _candidate_path.exists():
            return {
                "error": (
                    f"No stored credentials for '{account}'. Use the add_account MCP tool "
                    f"or run `ga4-mcp-add-account` to register this account."
                ),
                "property_id": pid,
                "account": account,
            }

    warnings = []
    data_streams_info = []
    data_stream_count = 0
    data_streams_probe_ok = False
    key_events_configured_count = 0
    key_events_probe_ok = False
    admin_list_permission_denied = False
    admin_client = None
    parent = f"properties/{pid}"

    # --- Shared credentials resolution ----------------------------------------------
    # Resolve once and reuse for both the Admin API client and the Data API client,
    # instead of calling resolve_credentials() separately from each probe block.
    #
    # IMPORTANT: if `account` was explicitly provided and credentials resolution
    # fails (corrupt token file, revoked refresh token, refresh network error),
    # we MUST return an error dict here — NOT fall through to the probe blocks
    # with shared_creds=None. Otherwise the probes would be constructed without
    # credentials, silently falling back to Application Default Credentials on
    # hosts that have ADC configured, and we'd cheerfully report diagnostics
    # for the WRONG identity while the broken OAuth account is labeled in the
    # response. The only case where shared_creds=None is acceptable is when
    # account was never provided (the default-credentials path).
    shared_creds = None
    if account:
        try:
            shared_creds = resolve_credentials(account)
        except Exception as e:
            return {
                "error": (
                    f"Failed to load credentials for account '{account}': {e}. "
                    f"The token file may be corrupt or the refresh token may have "
                    f"been revoked. Re-register the account via the add_account "
                    f"MCP tool or `ga4-mcp-add-account`."
                ),
                "property_id": pid,
                "account": account,
            }
    # else: account is None → default credentials path. Clients will resolve
    # ADC / service account on their own; shared_creds stays None.

    # --- Admin API client (shared by data streams + key events probes) --------------
    try:
        from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
        from google.analytics.admin_v1beta.types import (
            ListDataStreamsRequest,
            ListKeyEventsRequest,
        )
        from google.api_core.exceptions import PermissionDenied

        admin_client = (
            AnalyticsAdminServiceClient(credentials=shared_creds)
            if shared_creds else AnalyticsAdminServiceClient()
        )
    except ImportError:
        warnings.append("admin_api_unavailable: google-analytics-admin not installed.")
    except Exception as e:
        warnings.append(f"admin_client_failed: {e}")

    # --- Data streams probe (read-only, no edit scope required) ---------------------
    if admin_client is not None:
        try:
            for ds in admin_client.list_data_streams(
                request=ListDataStreamsRequest(parent=parent)
            ):
                data_stream_count += 1
                stream_entry = {
                    "name": ds.name,
                    "display_name": ds.display_name,
                    "type": ds.type_.name if hasattr(ds.type_, "name") else str(ds.type_),
                }
                if ds.type_.name == "WEB_DATA_STREAM":
                    stream_entry["default_uri"] = ds.web_stream_data.default_uri
                    stream_entry["measurement_id"] = ds.web_stream_data.measurement_id
                data_streams_info.append(stream_entry)
            data_streams_probe_ok = True
        except PermissionDenied as e:
            # Same Admin API permission gates list_key_events — no point attempting
            # the second probe; it will fail identically and clutter the warnings.
            admin_list_permission_denied = True
            data_streams_info = []
            data_stream_count = 0
            warnings.append(f"admin_list_permission_denied: {e}")
        except Exception as e:
            partial = data_stream_count
            data_streams_info = []
            data_stream_count = 0
            warnings.append(
                f"list_data_streams_failed_after_{partial}_streams: {e}"
                if partial > 0
                else f"list_data_streams_failed: {e}"
            )

    # Only emit the remediation warning when the probe actually succeeded.
    if data_streams_probe_ok and data_stream_count == 0:
        warnings.append(
            "no_data_stream_found: this property has no web/app data streams configured."
        )

    # --- Key events configuration probe (Admin API — the "is it set up?" question) --
    if admin_client is not None and not admin_list_permission_denied:
        try:
            for _ in admin_client.list_key_events(
                request=ListKeyEventsRequest(parent=parent)
            ):
                key_events_configured_count += 1
            key_events_probe_ok = True
        except PermissionDenied as e:
            admin_list_permission_denied = True
            warnings.append(f"admin_list_permission_denied: {e}")
        except Exception as e:
            warnings.append(f"list_key_events_failed: {e}")

    has_key_events_configured = key_events_probe_ok and key_events_configured_count > 0

    if key_events_probe_ok and key_events_configured_count == 0:
        warnings.append(
            "zero_key_events_configured: this property has no key events configured. "
            "Use ga4_create_key_event to mark events like 'form_submit' as conversions."
        )

    # --- Last-30-day activity probe (Data API — the "is anything firing?" question) -
    has_data = False
    has_key_event_activity_30d = False
    last_event_date = None
    data_probe_ok = False
    try:
        data_client = (
            BetaAnalyticsDataClient(credentials=shared_creds)
            if shared_creds else BetaAnalyticsDataClient()
        )
        req = RunReportRequest(
            property=f"properties/{pid}",
            dimensions=[Dimension(name="date")],
            metrics=[Metric(name="eventCount"), Metric(name="keyEvents")],
            date_ranges=[DateRange(start_date="30daysAgo", end_date="yesterday")],
            order_bys=[
                OrderBy(
                    dimension=OrderBy.DimensionOrderBy(dimension_name="date"),
                    desc=True,
                )
            ],
            limit=30,
        )
        response = data_client.run_report(request=req)
        for row in response.rows:
            date_str = row.dimension_values[0].value
            try:
                event_count = float(row.metric_values[0].value or 0)
            except ValueError:
                event_count = 0.0
            try:
                key_events = float(row.metric_values[1].value or 0)
            except ValueError:
                key_events = 0.0
            if event_count > 0:
                has_data = True
                if last_event_date is None or date_str > last_event_date:
                    last_event_date = date_str
            if key_events > 0:
                has_key_event_activity_30d = True
        data_probe_ok = True
    except Exception as e:
        warnings.append(f"data_api_probe_failed: {e}")

    # --- Inverse-state warnings -----------------------------------------------------
    # Activity warning is informational — configured key events may simply not have
    # fired yet on a low-traffic property. Only emit it when we have positive proof
    # (via Admin API) that key events ARE configured but no activity was recorded.
    if (
        data_probe_ok
        and has_key_events_configured
        and not has_key_event_activity_30d
    ):
        warnings.append(
            "zero_key_event_activity_30d: key events are configured but none fired "
            "in the last 30 days. This is normal on low-traffic properties; verify "
            "manually if the property has active traffic."
        )

    # Inverse state: configured=False but activity>0. A key event was likely deleted
    # from Admin; historical counts are still in the 30-day window and will drift off.
    if (
        data_probe_ok
        and key_events_probe_ok
        and not has_key_events_configured
        and has_key_event_activity_30d
    ):
        warnings.append(
            "stale_key_event_activity: key event activity was recorded in the last "
            "30 days but no key events are currently configured. A key event was "
            "likely deleted recently; historical counts will drift off as the "
            "30-day window rolls forward."
        )

    # THE Whitehat audit finding — the reason v3.2.0 exists. Traffic is flowing but
    # nothing is set up to be counted as a conversion.
    if (
        data_probe_ok
        and key_events_probe_ok
        and has_data
        and not has_key_events_configured
    ):
        warnings.append(
            "traffic_without_key_events: the property is receiving events but has no "
            "key events configured. Conversions will not be attributed. Use "
            "ga4_create_key_event to mark relevant events (e.g., 'form_submit') as "
            "key events."
        )

    # Data streams configured but the Data API shows zero activity in 30 days.
    if (
        data_probe_ok
        and data_streams_probe_ok
        and data_stream_count > 0
        and not has_data
    ):
        warnings.append(
            "data_streams_configured_but_silent: data streams are configured but "
            "zero events were recorded in the last 30 days. Check whether the "
            "tracking snippet is installed and firing on the target site/app."
        )

    # --- last_event_older_than_7_days heuristic -------------------------------------
    # last_event_date is a GA4 'date' dimension value (YYYYMMDD). GA4 returns dates
    # in the property's reporting time zone, not UTC; we treat it as a UTC calendar
    # date for this heuristic. At a 7-day threshold, up to ~14h of tz slop is
    # invisible — acceptable for a measurement-health diagnostic, would NOT be for
    # anything involving actual billing / reporting deadlines.
    if (
        last_event_date is not None
        and len(last_event_date) == 8
        and last_event_date.isdigit()
    ):
        try:
            from datetime import date, datetime, timedelta, timezone
            last_dt = date(
                int(last_event_date[:4]),
                int(last_event_date[4:6]),
                int(last_event_date[6:8]),
            )
            today_utc = datetime.now(timezone.utc).date()
            if last_dt < today_utc - timedelta(days=7):
                warnings.append(
                    f"last_event_older_than_7_days: last recorded event on "
                    f"{last_event_date} — check whether the tracking snippet is still firing."
                )
        except (ValueError, TypeError):
            pass

    # --- Scope info for the caller (so they know if remediation tools are available) ---
    granted = get_granted_scopes(account) if account else []
    can_edit = EDIT_SCOPE in granted if account else False

    # --- Tri-state status based on probe success ---
    # "success"         — all three probes succeeded, every field is trustworthy
    # "partial_success" — some probes failed; probe-dependent fields are None
    # "error"           — no probes succeeded; return a dedicated error dict
    #
    # Probe-dependent fields are nullable to distinguish "measured and is
    # zero" from "couldn't measure". The old behavior of returning 0/False
    # for probe failures was the root cause Codex flagged: an agent reading
    # status=success + has_data=False could not tell auth failure from a
    # real empty property.
    probes = {
        "data_streams_ok": data_streams_probe_ok,
        "key_events_ok": key_events_probe_ok,
        "data_api_ok": data_probe_ok,
    }

    if not (data_streams_probe_ok or key_events_probe_ok or data_probe_ok):
        # Complete failure — return error dict with diagnostic context.
        return {
            "error": (
                "ga4_health_check could not reach the property — all probes "
                "failed. Common causes: missing or invalid credentials, no "
                "access to the property, revoked refresh token, or an "
                "incorrect property_id. See `warnings` for the specific "
                "failure mode of each probe."
            ),
            "property_id": pid,
            "account": account or "default credentials",
            "probes": probes,
            "warnings": warnings,
        }

    all_probes_ok = (
        data_streams_probe_ok and key_events_probe_ok and data_probe_ok
    )
    status = "success" if all_probes_ok else "partial_success"

    # Probe-dependent fields: None when the underlying probe failed, so
    # callers can distinguish "unknown" from "measured zero/false".
    # can_edit is computed from stored scopes (not probe-dependent) so it
    # stays a definitive bool.
    return {
        "status": status,
        "property_id": pid,
        "has_data": has_data if data_probe_ok else None,
        "has_key_events_configured": (
            has_key_events_configured if key_events_probe_ok else None
        ),
        "key_events_configured_count": (
            key_events_configured_count if key_events_probe_ok else None
        ),
        "has_key_event_activity_30d": (
            has_key_event_activity_30d if data_probe_ok else None
        ),
        "data_stream_count": (
            data_stream_count if data_streams_probe_ok else None
        ),
        "last_event_received": last_event_date,  # already nullable
        # data_streams is None (not []) when the probe failed, matching the
        # data_stream_count nullable invariant. An empty list means "probe
        # succeeded, property has zero streams" — a real measured-zero
        # distinct from "unknown".
        "data_streams": data_streams_info if data_streams_probe_ok else None,
        "can_edit": can_edit,  # never probe-dependent
        "account": account or "default credentials",
        "probes": probes,
        "warnings": warnings,
    }
