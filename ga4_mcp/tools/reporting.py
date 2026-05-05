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

"""The core reporting tool for fetching GA4 data."""

import statistics
import sys
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Metric, RunReportRequest, FilterExpression,
    Filter, OrderBy, MetricAggregation
)
from ga4_mcp.coordinator import mcp
from ga4_mcp.tools.metadata import (
    _resolve_pid_or_error,
    _get_schema,
    _property_used_meta,
    _default_used_notice,
)
from ga4_mcp.auth import resolve_credentials

# Context-safety constants for the aggregation tools below. get_ga4_data has its own
# 2500-row estimation threshold and is not affected by these.
MAX_TOP_N = 200
DEFAULT_TOP_N_LANDING = 50
DEFAULT_TOP_N_COMPARE = 50
DEFAULT_FORM_EVENTS = ["form_submit", "form_start"]

def _get_smart_sorting(dimensions, metrics):
    """Determine optimal sorting strategy for relevance."""
    order_bys = []
    if "date" in dimensions:
        order_bys.append(OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"), desc=True))
    if metrics:
        order_bys.append(OrderBy(metric=OrderBy.MetricOrderBy(metric_name=metrics[0]), desc=True))
    return order_bys

def _should_aggregate(dimensions):
    """Detect when server-side aggregation would be beneficial."""
    return "date" not in dimensions


def _extract_filter_dimensions(node: dict) -> set:
    """Recursively extract dimension field_name values from a filter expression dict."""
    if not isinstance(node, dict):
        return set()

    dims = set()
    if "filter" in node and isinstance(node["filter"], dict):
        field = node["filter"].get("field_name")
        if field:
            dims.add(field)

    for key in ("and_group", "or_group"):
        group = node.get(key, {})
        if isinstance(group, dict):
            for expr in group.get("expressions", []):
                dims |= _extract_filter_dimensions(expr)

    if "not_expression" in node:
        dims |= _extract_filter_dimensions(node["not_expression"])

    return dims


@mcp.tool()
def get_ga4_data(
    dimensions: list[str] = None,
    metrics: list[str] = None,
    date_range_start: str = "7daysAgo",
    date_range_end: str = "yesterday",
    dimension_filter: dict = None,
    limit: int = 1000,
    estimate_only: bool = False,
    proceed_with_large_dataset: bool = False,
    enable_aggregation: bool = True,
    property_id: str = None,
    account: str = None
):
    """
    Retrieve GA4 data with built-in intelligence for better and safer results.

    This tool is a powerful wrapper around the Google Analytics Data API. It not only
    fetches data but also includes "smart" features to protect against context window
    overloads and to provide more relevant results by default.

    **Smart Features:**
    - **Data Volume Protection:** Before running a query that could produce a huge
      number of rows, the tool runs a quick estimate. If the row count exceeds a
      safe threshold (2500 rows), it will return a warning with suggestions instead
      of the data. You can override this by setting `proceed_with_large_dataset=True`.
    - **Automatic Server-Side Aggregation:** If your query does not involve a time
      dimension (like 'date'), the tool automatically asks the GA4 API to return
      aggregated totals. This is more efficient and provides cleaner results. You
      can disable this by setting `enable_aggregation=False`.
    - **Intelligent Sorting:** Results are automatically sorted by date (most recent
      first) and the primary metric (highest value first) to show the most
      relevant data at the top.

    Args:
        dimensions: List of GA4 dimensions (e.g., ["date", "city"]). Defaults to ["date"].
        metrics: List of GA4 metrics (e.g., ["totalUsers", "sessions"]). Defaults to ["totalUsers", "newUsers", "sessions"].
        date_range_start: Start date in YYYY-MM-DD format or relative date ('7daysAgo').
        date_range_end: End date in YYYY-MM-DD format or relative date ('yesterday').
        dimension_filter: (Optional) A dictionary representing a GA4 FilterExpression
                          to apply to the dimensions. See GA4 API docs for structure.
        limit: (Optional) Maximum number of rows to return. Defaults to 1000.
        estimate_only: (Optional) If True, returns only the estimated row count
                       without fetching the full dataset.
        proceed_with_large_dataset: (Optional) Set to True to bypass the data volume
                                    warning and execute the query anyway.
        enable_aggregation: (Optional) If True, uses server-side aggregation when
                            beneficial. Defaults to True.
        property_id: (Optional) GA4 property ID (numeric) to query. If omitted, uses
            GA4_PROPERTY_ID if set. Pass any property_id from list_properties() to
            query a specific property your account can access — you do not need a
            configured default. If that property was discovered via
            list_properties(account="..."), pass the same account here.
        account: (Optional) Registered OAuth account email used as credentials. If
            omitted, uses default credentials only — it does not search all registered
            accounts. Properties are credential-scoped: if a property was returned by
            list_properties(account="user@example.com"), pass the same account here.
            Use list_accounts() to see available credential accounts. Do not pass the
            literal string "default credentials".
    """
    if dimensions is None:
        dimensions = ["date"]
    if metrics is None:
        metrics = ["totalUsers", "newUsers", "sessions"]

    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    # Build once and stamp on every non-error return path that ran past
    # property resolution — including estimate_only and the large-dataset
    # warning. Otherwise agents wouldn't know which property the warning
    # was talking about when they omitted property_id.
    property_used = _property_used_meta(pid, was_explicit, account)
    default_notice = _default_used_notice(was_explicit, pid, account)

    def _annotate(response):
        response["property_used"] = property_used
        if default_notice:
            response["notice"] = default_notice
        return response

    try:
        schema = _get_schema(pid, account)
    except Exception as e:
        return {"error": f"Failed to load schema for property '{pid}': {e}"}

    try:
        # --- Input Parsing and Validation ---
        parsed_dimensions = dimensions if isinstance(dimensions, list) else [d.strip() for d in dimensions.split(',')]
        parsed_metrics = metrics if isinstance(metrics, list) else [m.strip() for m in metrics.split(',')]

        if not parsed_dimensions:
            return {"error": "Dimensions list cannot be empty."}
        if not parsed_metrics:
            return {"error": "Metrics list cannot be empty."}

        valid_dims = schema["dimensions"].keys()
        valid_mets = schema["metrics"].keys()
        for dim in parsed_dimensions:
            if dim not in valid_dims:
                return {"error": f"Invalid dimension: '{dim}'. Use list_dimension_categories() to see available dimensions."}
        for met in parsed_metrics:
            if met not in valid_mets:
                return {"error": f"Invalid metric: '{met}'. Use list_metric_categories() to see available metrics."}

        # --- Filter Expression Building ---
        filter_expression = None
        if dimension_filter:
            # Validate filter dimensions against schema
            filter_dims = _extract_filter_dimensions(dimension_filter)
            for fdim in filter_dims:
                if fdim not in valid_dims:
                    return {"error": f"Invalid dimension in filter: '{fdim}'. Use search_schema() to find valid dimensions."}
            try:
                filter_expression = FilterExpression(dimension_filter)
            except Exception as e:
                return {"error": f"Invalid dimension_filter structure: {e}"}

        # --- API Client and Request Objects ---
        creds = resolve_credentials(account)
        client = BetaAnalyticsDataClient(credentials=creds) if creds else BetaAnalyticsDataClient()
        dimension_objects = [Dimension(name=d) for d in parsed_dimensions]
        metric_objects = [Metric(name=m) for m in parsed_metrics]
        date_range_object = DateRange(start_date=date_range_start, end_date=date_range_end)

        # --- Row Count Estimation ---
        if not proceed_with_large_dataset or estimate_only:
            try:
                estimation_req = RunReportRequest(
                    property=f"properties/{pid}",
                    dimensions=dimension_objects,
                    metrics=metric_objects,
                    date_ranges=[date_range_object],
                    dimension_filter=filter_expression,
                    limit=1
                )
                estimation_res = client.run_report(request=estimation_req)
                estimated_rows = estimation_res.row_count

                if estimate_only:
                    return _annotate({"estimated_rows": estimated_rows})

                if estimated_rows > 2500:
                    return _annotate({
                        "warning": "Query will return a large dataset.",
                        "estimated_rows": estimated_rows,
                        "suggestions": [
                            "Reduce the date range.",
                            "Add or refine the dimension_filter.",
                            "Use fewer dimensions.",
                            "Or, re-run with proceed_with_large_dataset=True to fetch the data anyway."
                        ]
                    })
            except Exception as e:
                print(f"WARNING: Row count estimation failed: {e}", file=sys.stderr)
                if estimate_only:
                    return {"error": f"Could not estimate row count: {e}"}
                if not proceed_with_large_dataset:
                    return _annotate({
                        "warning": "Could not safely estimate dataset size.",
                        "estimation_error": str(e),
                        "suggestions": [
                            "Retry the query.",
                            "Or re-run with proceed_with_large_dataset=True to bypass the safety check."
                        ]
                    })

        # --- Main GA4 API Call ---
        request = RunReportRequest(
            property=f"properties/{pid}",
            dimensions=dimension_objects,
            metrics=metric_objects,
            date_ranges=[date_range_object],
            dimension_filter=filter_expression,
            limit=limit,
            order_bys=_get_smart_sorting(parsed_dimensions, parsed_metrics),
            metric_aggregations=[MetricAggregation.TOTAL] if enable_aggregation and _should_aggregate(parsed_dimensions) else None
        )
        response = client.run_report(request=request)

        # --- Response Formatting ---
        result = []
        for row in response.rows:
            data_row = {}
            for i, dim_header in enumerate(response.dimension_headers):
                data_row[dim_header.name] = row.dimension_values[i].value
            for i, met_header in enumerate(response.metric_headers):
                data_row[met_header.name] = row.metric_values[i].value
            result.append(data_row)

        return _annotate({
            "data": result,
            "metadata": {
                "total_rows_in_source": response.row_count,
                "returned_rows": len(result),
            }
        })
    except Exception as e:
        error_message = f"Error fetching GA4 data: {str(e)}"
        print(error_message, file=sys.stderr)
        details = getattr(e, "details", None)
        if callable(details):
            try:
                details = details()
            except Exception:
                details = None
        if details:
            error_message += f" Details: {details}"
        return {"error": error_message}


# ---------- Context-safe aggregation tools ----------


def _build_client(account):
    creds = resolve_credentials(account)
    return BetaAnalyticsDataClient(credentials=creds) if creds else BetaAnalyticsDataClient()


def _to_number(value):
    """Best-effort numeric coercion for GA4 metric string values."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_rows(response):
    """Convert a RunReportResponse into a list of dicts keyed by dimension/metric names."""
    rows = []
    for row in response.rows:
        data_row = {}
        for i, dim_header in enumerate(response.dimension_headers):
            data_row[dim_header.name] = row.dimension_values[i].value
        for i, met_header in enumerate(response.metric_headers):
            data_row[met_header.name] = row.metric_values[i].value
        rows.append(data_row)
    return rows


def _extract_totals(response):
    """Pull site-wide totals out of a RunReportResponse.

    Returns a tuple ``(totals_dict, available)``:

    - ``available=False`` means the response has NO totals row — either
      ``metric_aggregations=[TOTAL]`` wasn't requested on the call, or GA4
      returned an empty result set. Callers must treat the dict as
      unknown (not zero) in that case.
    - ``available=True`` means a totals row exists. Individual metric
      values in the dict may still be ``None`` if GA4 omitted a specific
      metric value — callers must NOT fall back to 0 for nullable values,
      that silently encodes "unknown" as a definitive "zero" and was the
      root cause of several misleading audit results in v3.2.0 drafts.
    """
    if not response.totals:
        return {}, False
    totals = {}
    total_row = response.totals[0]
    for i, met_header in enumerate(response.metric_headers):
        val = total_row.metric_values[i].value
        if val is None or val == "":
            totals[met_header.name] = None
        else:
            try:
                totals[met_header.name] = float(val)
            except (TypeError, ValueError):
                totals[met_header.name] = None
    return totals, True


@mcp.tool()
def ga4_get_landing_page_summary(
    date_range_start: str = "28daysAgo",
    date_range_end: str = "yesterday",
    top_n: int = 50,
    form_event_names: list = None,
    property_id: str = None,
    account: str = None,
):
    """Context-safe landing-page audit summary with diagnostics.

    Returns the top N landing pages by sessions plus site-wide totals (covering the
    full property, not just the top N slice) computed via GA4 server-side
    MetricAggregation.TOTAL. Adds diagnostics for common audit red flags:

    - ``rot_flag`` per row: sessions above median AND engagement_rate below median
    - ``zero_key_events_warning``: True when the property has zero keyEvents in the
      period AND has at least one session (warnings are gated on ``total_sessions > 0``
      to avoid false alarms on empty properties / empty date ranges)
    - ``low_engagement_warning``: True when site-wide engagement_rate < 0.35 AND
      the property has sessions
    - ``high_not_set_pct``: FULL-PROPERTY share of sessions on landing_page ==
      "(not set)", not a share of the top_n slice. May be ``None`` if the probe
      was unmeasurable (see ``high_not_set_pct_source`` and ``probes.not_set_probe_ok``
      to distinguish "couldn't measure" from "measured zero"). The warning only
      fires when the value is a real measurement, never on ``None``.

    **Uniform nullable totals:** ALL ``totals.*`` count and rate fields
    (``sessions``, ``engaged_sessions``, ``engagement_rate``, ``key_events``,
    ``total_users``) are ``None`` when GA4 returned no totals row — empty-data
    and "unknown" are distinct states. Callers MUST NOT default missing totals
    to 0: a response with ``sessions: None`` means "we couldn't measure it"
    while ``sessions: 0`` means "we measured and the property has zero
    sessions in the period". ``probes.totals_available`` is the single
    source of truth for whether any totals were returned.

    **`totals.form_events` is also nullable**: it is a ``dict[str, int]``
    mapping each requested form event name to its measured firing count
    when the form events probe succeeded, or ``None`` when the probe failed.
    An event that didn't fire in the period gets a measured-zero entry in
    the dict (GA4 omits all-zero rows from the response, so "event present
    in results with count" vs. "event absent from results" is a valid
    distinction and we fill absent entries with 0). Check
    ``probes.form_events_probe_ok`` to distinguish "probe failed" from
    "probe succeeded and all events are zero".

    Hard-capped at top_n <= 200 so the response is always context-safe (~10k tokens max).

    Args:
        date_range_start: Start date ("28daysAgo", "2026-01-01", etc). Default "28daysAgo".
        date_range_end: End date. Default "yesterday".
        top_n: Number of top landing pages to return. Clamped to 1..200. Default 50.
        form_event_names: Event names counted as form conversions in totals. Default
                          ["form_submit", "form_start"]. Override for properties that use
                          "generate_lead", "contact", "submit_application", etc.
        property_id: (Optional) GA4 property ID (numeric) to query. If omitted, uses
            GA4_PROPERTY_ID if set. Pass any property_id from list_properties() to
            query a specific property your account can access — you do not need a
            configured default. If that property was discovered via
            list_properties(account="..."), pass the same account here.
        account: (Optional) Registered OAuth account email used as credentials. If
            omitted, uses default credentials only — it does not search all registered
            accounts. Properties are credential-scoped: if a property was returned by
            list_properties(account="user@example.com"), pass the same account here.
            Use list_accounts() to see available credential accounts. Do not pass the
            literal string "default credentials".
    """
    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    top_n = max(1, min(int(top_n or DEFAULT_TOP_N_LANDING), MAX_TOP_N))
    form_events = list(form_event_names) if form_event_names else list(DEFAULT_FORM_EVENTS)

    try:
        client = _build_client(account)
        date_range = DateRange(start_date=date_range_start, end_date=date_range_end)

        # --- Main landing page report with site-wide totals in a single call ---
        main_req = RunReportRequest(
            property=f"properties/{pid}",
            dimensions=[Dimension(name="landingPagePlusQueryString")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="engagedSessions"),
                Metric(name="engagementRate"),
                Metric(name="averageSessionDuration"),
                Metric(name="keyEvents"),
                Metric(name="totalUsers"),
            ],
            date_ranges=[date_range],
            limit=top_n,
            order_bys=[
                OrderBy(
                    metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                    desc=True,
                )
            ],
            metric_aggregations=[MetricAggregation.TOTAL],
        )
        main_resp = client.run_report(request=main_req)
        raw_rows = _format_rows(main_resp)
        totals, totals_available = _extract_totals(main_resp)
    except Exception as e:
        return {"error": f"Failed to fetch landing page summary: {e}"}

    # --- Form events — cheap second call, non-fatal on failure ---
    # Top-level nullable semantics: the form events query is all-or-nothing.
    # On successful query, form_counts is a dict of measured counts (events
    # that didn't fire are measured-zero because GA4's default omits all-zero
    # rows). On any exception, form_counts stays None — we know nothing.
    # Per-event nullability was considered but adds no information: a failed
    # query means every event is unknown, so a top-level None is strictly
    # simpler and matches the M3 `data_streams: None` pattern.
    form_warnings = []
    form_counts = None  # dict[str, int] | None — None means probe failed
    form_events_probe_ok = False
    try:
        form_req = RunReportRequest(
            property=f"properties/{pid}",
            dimensions=[Dimension(name="eventName")],
            metrics=[Metric(name="eventCount")],
            date_ranges=[date_range],
            dimension_filter=FilterExpression(
                filter=Filter(
                    field_name="eventName",
                    in_list_filter=Filter.InListFilter(values=form_events),
                )
            ),
            limit=len(form_events) + 2,
        )
        form_resp = client.run_report(request=form_req)
        # Build `measured` in a LOCAL variable so a mid-iteration exception
        # can't leave form_counts partially populated. Only assign to
        # form_counts after the loop completes cleanly.
        measured = {name: 0 for name in form_events}
        for row in _format_rows(form_resp):
            name = row.get("eventName")
            if name in measured:
                measured[name] = int(_to_number(row.get("eventCount")))
        form_counts = measured
        form_events_probe_ok = True
    except Exception as e:
        # Explicit reset — form_counts was already None at init, but this
        # defends against any future change to the default initialization.
        form_counts = None
        form_events_probe_ok = False
        form_warnings.append(f"form_events_query_failed: {e}")

    # --- Transform rows with diagnostics ---
    shaped_rows = []
    for r in raw_rows:
        sessions = _to_number(r.get("sessions"))
        engaged = _to_number(r.get("engagedSessions"))
        engagement_rate = _to_number(r.get("engagementRate"))
        avg_duration = _to_number(r.get("averageSessionDuration"))
        key_events = _to_number(r.get("keyEvents"))
        users = _to_number(r.get("totalUsers"))
        shaped_rows.append({
            "landing_page": r.get("landingPagePlusQueryString", ""),
            "sessions": int(sessions),
            "engaged_sessions": int(engaged),
            "engagement_rate": round(engagement_rate, 4),
            "avg_session_duration": round(avg_duration, 2),
            "key_events": int(key_events),
            "total_users": int(users),
        })

    if shaped_rows:
        sessions_values = [row["sessions"] for row in shaped_rows]
        engagement_values = [row["engagement_rate"] for row in shaped_rows]
        # statistics.median returns float for even-sized lists (e.g.
        # median([1, 2]) == 1.5), so median_sessions is int | float
        # here, NOT purely int. Downstream callers must not assume int.
        median_sessions = statistics.median(sessions_values)
        median_engagement = statistics.median(engagement_values)
        for row in shaped_rows:
            row["rot_flag"] = (
                row["sessions"] > median_sessions
                and row["engagement_rate"] < median_engagement
            )
    else:
        # Median of zero rows is undefined, not zero. Using 0 here used to
        # falsely imply "the median page has 0 sessions" for empty slices.
        median_sessions = None
        median_engagement = None

    # --- Pull totals with UNIFORM nullable semantics ---
    # ALL totals preserve None when unmeasurable — never fall back to 0.
    # Zero-fallback silently encodes "unknown" as "measured zero", which
    # is exactly the bug open-gem and Codex flagged. Empty property
    # (totals row present with sessions=0) and missing totals row
    # (totals_available=False) are distinct states and the response must
    # make them distinguishable.
    #
    # _extract_totals() returns {} when totals_available is False, so
    # totals.get(key) naturally returns None in that case — no need for
    # a separate totals_available guard here.
    def _coerce_int(v):
        return int(v) if v is not None else None

    total_sessions = _coerce_int(totals.get("sessions"))
    total_engaged = _coerce_int(totals.get("engagedSessions"))
    _engagement_rate_raw = totals.get("engagementRate")
    total_engagement_rate = (
        round(_engagement_rate_raw, 4) if _engagement_rate_raw is not None else None
    )
    total_key_events = _coerce_int(totals.get("keyEvents"))
    total_users = _coerce_int(totals.get("totalUsers"))

    # --- Full-property (not set) share via conditional probe ---
    # Previously this was computed from the top_n slice, which gave false
    # clean bills of health when "(not set)" fell outside the top N (and
    # inflated ratios when "(not set)" happened to be one of the top rows).
    # New strategy:
    #   1. If "(not set)" already appears in the top_n rows from the main
    #      call, use its sessions directly (no extra API call).
    #   2. Otherwise, issue a single targeted filtered query for the
    #      "(not set)" row across the full period.
    #   3. If either measurement succeeds and total_sessions > 0, compute
    #      the ratio. Otherwise high_not_set_pct stays None to distinguish
    #      "unknown" from "measured zero".
    high_not_set_pct = None  # float | None
    high_not_set_pct_source = "unavailable"  # "top_rows" | "filtered_probe" | "unavailable"
    not_set_sessions_full = None

    top_n_not_set_rows = [r for r in raw_rows if r.get("landingPagePlusQueryString") == "(not set)"]
    if top_n_not_set_rows:
        # Already in the main response — no extra API call needed
        ns_val = _to_number(top_n_not_set_rows[0].get("sessions"))
        not_set_sessions_full = int(ns_val)
        high_not_set_pct_source = "top_rows"
    else:
        # Not in the top_n slice — issue a targeted probe with EXACT match
        # so we don't rely on protobuf defaults for match_type.
        try:
            not_set_req = RunReportRequest(
                property=f"properties/{pid}",
                dimensions=[Dimension(name="landingPagePlusQueryString")],
                metrics=[Metric(name="sessions")],
                date_ranges=[date_range],
                dimension_filter=FilterExpression(
                    filter=Filter(
                        field_name="landingPagePlusQueryString",
                        string_filter=Filter.StringFilter(
                            match_type=Filter.StringFilter.MatchType.EXACT,
                            value="(not set)",
                        ),
                    )
                ),
                limit=1,
            )
            not_set_resp = client.run_report(request=not_set_req)
            not_set_rows_probe = _format_rows(not_set_resp)
            if not_set_rows_probe:
                not_set_sessions_full = int(
                    _to_number(not_set_rows_probe[0].get("sessions"))
                )
            else:
                not_set_sessions_full = 0
            high_not_set_pct_source = "filtered_probe"
        except Exception as e:
            form_warnings.append(f"not_set_probe_failed: {e}")
            high_not_set_pct_source = "unavailable"

    if (
        not_set_sessions_full is not None
        and total_sessions is not None
        and total_sessions > 0
    ):
        high_not_set_pct = not_set_sessions_full / total_sessions

    warnings = list(form_warnings)

    # --- Warnings gated on having definitive positive session data ---
    # Both None (totals unavailable — we don't know) AND 0 (empty property
    # / empty date range — we measured and there's nothing) skip warnings.
    # This prevents false alarms in two distinct cases that both fail the
    # "we can make a meaningful judgment" test.
    #
    # Note: `total_key_events == 0` is safely False when total_key_events
    # is None (None == 0 evaluates False in Python), so no extra None
    # check is needed on the count metric here.
    if total_sessions is not None and total_sessions > 0:
        zero_key_events_warning = total_key_events == 0
        low_engagement_warning = (
            total_engagement_rate is not None and total_engagement_rate < 0.35
        )
    else:
        zero_key_events_warning = False
        low_engagement_warning = False

    if zero_key_events_warning:
        warnings.append(
            "zero_key_events_warning: this property has recorded 0 key events in the "
            "selected period. Verify that key events are configured under Admin → Events "
            "→ Mark as key event (use ga4_list_key_events and ga4_create_key_event to fix)."
        )
    if low_engagement_warning:
        warnings.append(
            f"low_engagement_warning: site-wide engagement_rate {total_engagement_rate} "
            "is below 0.35 — investigate landing page quality, traffic source mix, and "
            "tracking accuracy."
        )
    # high_not_set_pct warning ONLY fires when we have a real measurement.
    # An unmeasurable probe (None) must NOT trigger the warning — that was
    # the previous bug: we reported 0.0 as a clean bill of health.
    high_not_set_pct_warning = (
        high_not_set_pct is not None and high_not_set_pct > 0.05
    )
    if high_not_set_pct_warning:
        warnings.append(
            f"high_not_set_pct: {high_not_set_pct:.1%} of sessions have landing_page == "
            "'(not set)'. This usually means events are firing before page_view, or "
            "sessions are starting without a page_view at all."
        )

    response = {
        "status": "success",
        "rows": shaped_rows,
        "totals": {
            "sessions": total_sessions,
            "engaged_sessions": total_engaged,
            # Ratio metrics preserve None when unmeasurable — do NOT
            # coerce to 0.0, that's what gave us false low_engagement
            # warnings on empty properties.
            "engagement_rate": total_engagement_rate,
            "key_events": total_key_events,
            "total_users": total_users,
            "form_events": form_counts,
            # None when the (not set) probe was unmeasurable (see
            # high_not_set_pct_source and probes.not_set_probe_ok to
            # distinguish "couldn't measure" from "measured zero").
            "high_not_set_pct": (
                round(high_not_set_pct, 4) if high_not_set_pct is not None else None
            ),
            "high_not_set_pct_source": high_not_set_pct_source,
            # median_sessions is int | float | None (float on even-sized
            # lists, None on empty shaped_rows). No round() needed — callers
            # get the raw median.
            "median_sessions": median_sessions,
            "median_engagement_rate": (
                round(median_engagement, 4) if median_engagement is not None else None
            ),
        },
        "diagnostics": {
            "zero_key_events_warning": zero_key_events_warning,
            "low_engagement_warning": low_engagement_warning,
            "high_not_set_pct_warning": high_not_set_pct_warning,
        },
        "probes": {
            "totals_available": totals_available,
            "not_set_probe_ok": high_not_set_pct_source != "unavailable",
            "form_events_probe_ok": form_events_probe_ok,
        },
        "warnings": warnings,
        "property_used": _property_used_meta(pid, was_explicit, account),
        "metadata": {
            "property_id": pid,
            "date_range": {"start": date_range_start, "end": date_range_end},
            "top_n": top_n,
            "form_event_names": form_events,
            "returned_rows": len(shaped_rows),
        },
    }
    notice = _default_used_notice(was_explicit, pid, account)
    if notice:
        response["notice"] = notice
    return response


@mcp.tool()
def ga4_compare_periods(
    dimensions: list,
    metrics: list,
    period_a_start: str,
    period_a_end: str,
    period_b_start: str,
    period_b_end: str,
    sort_by_delta: str,
    top_n: int = 50,
    dimension_filter: dict = None,
    property_id: str = None,
    account: str = None,
):
    """Compare two date ranges and return the largest absolute-delta rows.

    Runs two server-side aggregated reports (one per period) and outer-joins them in
    Python on the dimension tuple, computing absolute and percent deltas for each metric.

    **Known limitation — long-tail join truncation:** Each period is independently
    limited to ``top_n * 4`` rows before the join. A dimension value ranked #1 in period A
    but #500 in period B will appear with ``b = 0`` and an inflated absolute delta. For
    volatile long-tail dimensions, use larger ``top_n`` and treat edge rows as approximate,
    not exact. The response includes ``join_warning: true`` when any returned row has
    ``a == 0`` or ``b == 0``.

    **Totals semantics:** ``totals_delta`` comes from GA4's server-side
    ``MetricAggregation.TOTAL`` (the full filtered report for each period), NOT a
    Python sum of the ``rows`` returned. This means it is valid for ratio and
    duration metrics (``engagementRate``, ``averageSessionDuration``, ``bounceRate``)
    where summing per-row values would be meaningless. Per-metric ``a``, ``b``,
    ``abs_delta``, ``pct_delta`` may be ``None`` if GA4 omitted the totals row or a
    specific metric for one of the periods — check ``probes.period_a_totals_available``
    and ``probes.period_b_totals_available``. Do NOT expect ``sum(rows[m])`` to equal
    ``totals_delta[m]`` — they are intentionally different scopes (truncated top-N
    vs. full filtered report).

    Args:
        dimensions: List of up to 3 GA4 dimension names (e.g., ["landingPagePlusQueryString"]).
        metrics: List of GA4 metric names (e.g., ["sessions", "engagedSessions"]).
        period_a_start / period_a_end: Baseline period.
        period_b_start / period_b_end: Comparison period.
        sort_by_delta: Metric name (must be in ``metrics``) whose absolute delta is used to sort rows.
        top_n: Rows to return after sorting. Clamped to 1..200. Default 50.
        dimension_filter: (Optional) Same FilterExpression dict shape as get_ga4_data.
        property_id: (Optional) GA4 property ID (numeric) to query. If omitted, uses
            GA4_PROPERTY_ID if set. Pass any property_id from list_properties() to
            query a specific property your account can access — you do not need a
            configured default. If that property was discovered via
            list_properties(account="..."), pass the same account here.
        account: (Optional) Registered OAuth account email used as credentials. If
            omitted, uses default credentials only — it does not search all registered
            accounts. Properties are credential-scoped: if a property was returned by
            list_properties(account="user@example.com"), pass the same account here.
            Use list_accounts() to see available credential accounts. Do not pass the
            literal string "default credentials".
    """
    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err

    if not dimensions:
        return {"error": "dimensions must contain at least one GA4 dimension name."}
    if len(dimensions) > 3:
        return {"error": "dimensions is capped at 3 to keep row cardinality under control."}
    if not metrics:
        return {"error": "metrics must contain at least one GA4 metric name."}
    if sort_by_delta not in metrics:
        return {"error": f"sort_by_delta '{sort_by_delta}' must be one of the requested metrics: {metrics}."}

    top_n = max(1, min(int(top_n or DEFAULT_TOP_N_COMPARE), MAX_TOP_N))
    fetch_limit = top_n * 4

    # Schema validation — reuse the cached schema.
    try:
        schema = _get_schema(pid, account)
    except Exception as e:
        return {"error": f"Failed to load schema for property '{pid}': {e}"}

    valid_dims = schema["dimensions"].keys()
    valid_mets = schema["metrics"].keys()
    for dim in dimensions:
        if dim not in valid_dims:
            return {"error": f"Invalid dimension: '{dim}'."}
    for met in metrics:
        if met not in valid_mets:
            return {"error": f"Invalid metric: '{met}'."}

    # Build filter if provided.
    filter_expression = None
    if dimension_filter:
        filter_dims = _extract_filter_dimensions(dimension_filter)
        for fdim in filter_dims:
            if fdim not in valid_dims:
                return {"error": f"Invalid dimension in filter: '{fdim}'."}
        try:
            filter_expression = FilterExpression(dimension_filter)
        except Exception as e:
            return {"error": f"Invalid dimension_filter structure: {e}"}

    dim_objects = [Dimension(name=d) for d in dimensions]
    metric_objects = [Metric(name=m) for m in metrics]
    order_bys = [
        OrderBy(metric=OrderBy.MetricOrderBy(metric_name=sort_by_delta), desc=True)
    ]

    def run_period(start, end):
        req = RunReportRequest(
            property=f"properties/{pid}",
            dimensions=dim_objects,
            metrics=metric_objects,
            date_ranges=[DateRange(start_date=start, end_date=end)],
            dimension_filter=filter_expression,
            limit=fetch_limit,
            order_bys=order_bys,
            # Request server-side full-period totals so totals_delta below
            # uses GA4's authoritative aggregation for each metric instead
            # of a Python sum across per-row values. The Python sum was
            # valid for counts but nonsense for ratio / duration metrics
            # (engagementRate, bounceRate, averageSessionDuration, etc.)
            # where two 50% rows would sum to 1.0.
            metric_aggregations=[MetricAggregation.TOTAL],
        )
        resp = client.run_report(request=req)
        totals, available = _extract_totals(resp)
        return _format_rows(resp), totals, available

    try:
        client = _build_client(account)
        a_rows, a_totals, a_totals_available = run_period(period_a_start, period_a_end)
        b_rows, b_totals, b_totals_available = run_period(period_b_start, period_b_end)
    except Exception as e:
        return {"error": f"Failed to fetch comparison data: {e}"}

    def index_by_dims(rows):
        idx = {}
        for r in rows:
            key = tuple(r.get(d, "") for d in dimensions)
            idx[key] = r
        return idx

    a_idx = index_by_dims(a_rows)
    b_idx = index_by_dims(b_rows)
    all_keys = set(a_idx) | set(b_idx)

    delta_rows = []
    for key in all_keys:
        a = a_idx.get(key, {})
        b = b_idx.get(key, {})
        metric_deltas = {}
        for m in metrics:
            av = _to_number(a.get(m))
            bv = _to_number(b.get(m))
            abs_delta = bv - av
            pct_delta = (abs_delta / av) if av else None
            metric_deltas[m] = {
                "a": av,
                "b": bv,
                "abs_delta": abs_delta,
                "pct_delta": round(pct_delta, 4) if pct_delta is not None else None,
            }
        delta_rows.append({
            "dimensions": dict(zip(dimensions, key)),
            "metrics": metric_deltas,
        })

    delta_rows.sort(
        key=lambda r: abs(r["metrics"][sort_by_delta]["abs_delta"]),
        reverse=True,
    )
    top_rows = delta_rows[:top_n]

    join_warning = any(
        row["metrics"][sort_by_delta]["a"] == 0 or row["metrics"][sort_by_delta]["b"] == 0
        for row in top_rows
    )

    # Server-computed totals_delta — uses GA4's report-wide totals row
    # rather than summing per-row values. This is valid for ratio and
    # duration metrics (engagementRate, averageSessionDuration, bounceRate,
    # etc.) where summing per-row values produces meaningless numbers.
    # Per-metric values are None when GA4 omitted them or the totals row
    # wasn't available for a given period.
    totals_delta = {}
    for m in metrics:
        av = a_totals.get(m) if a_totals_available else None
        bv = b_totals.get(m) if b_totals_available else None
        if av is None or bv is None:
            totals_delta[m] = {
                "a": av,
                "b": bv,
                "abs_delta": None,
                "pct_delta": None,
            }
        else:
            abs_delta = bv - av
            pct_delta = (abs_delta / av) if av else None
            totals_delta[m] = {
                "a": av,
                "b": bv,
                "abs_delta": abs_delta,
                "pct_delta": round(pct_delta, 4) if pct_delta is not None else None,
            }

    response = {
        "status": "success",
        "rows": top_rows,
        "totals_delta": totals_delta,
        "join_warning": join_warning,
        "probes": {
            "period_a_totals_available": a_totals_available,
            "period_b_totals_available": b_totals_available,
        },
        "property_used": _property_used_meta(pid, was_explicit, account),
        "metadata": {
            "property_id": pid,
            "period_a": {"start": period_a_start, "end": period_a_end},
            "period_b": {"start": period_b_start, "end": period_b_end},
            "dimensions": list(dimensions),
            "metrics": list(metrics),
            "sort_by_delta": sort_by_delta,
            "top_n": top_n,
            "fetch_limit_per_period": fetch_limit,
            "returned_rows": len(top_rows),
            "totals_scope": "full_filtered_report",
            "join_notes": (
                "Rows where a or b is exactly 0 may reflect the row falling outside "
                "the top-fetch_limit_per_period window for that period, not a true zero. "
                "Use larger top_n for long-tail analyses. Note that `rows` are a "
                "truncated top-N join while `totals_delta` covers the full filtered "
                "report — do NOT expect sum(rows[m]) to equal totals_delta[m]."
            ),
        },
    }
    notice = _default_used_notice(was_explicit, pid, account)
    if notice:
        response["notice"] = notice
    return response
