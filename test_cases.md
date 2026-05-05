# GA4 MCP Server - Test Cases

This document outlines a series of test cases to validate the accuracy, efficiency, and feature-set of the refactored GA4 MCP server.

## Category 1: Data Granularity & Aggregation Tests

**Objective:** To verify that the server requests and returns data at the correct level of time-based aggregation, directly addressing the concern of receiving monthly totals vs. daily breakdowns.

---

### Test Case 1.1: Monthly Granularity over a Long Period

*   **Goal:** Confirm that using `month` as a dimension for a multi-month period returns exactly one row per month per item, not daily data.
*   **Query via MCP:**
    *   **Tool:** `get_ga4_data`
    *   **dimensions:** `["month", "pagePath"]`
    *   **metrics:** `["sessions", "totalUsers"]`
    *   **date_range_start:** A date 16 months ago (e.g., `"2024-01-01"`)
    *   **date_range_end:** `"yesterday"`
    *   **dimension_filter:** A filter for 5 high-traffic pages on your site.
      ```json
      {
        "filter": {
          "fieldName": "pagePath",
          "inListFilter": {
            "values": ["/your/page/path1", "/your/page/path2", "/your/page/path3", "/your/paga/path4", "/your/page/path5"]
          }
        }
      }
      ```
*   **Expected Result:** The number of rows in the output should be `(number of months in range) * 5`. For a 16-month period, you should get **~80 rows**, not thousands of rows of daily data. Each row should represent the total metrics for a given page for an entire month.

---

### Test Case 1.2: Daily Granularity

*   **Goal:** Confirm that using `date` as a dimension returns daily data points.
*   **Query via MCP:**
    *   **Tool:** `get_ga4_data`
    *   **dimensions:** `["date", "pagePath"]`
    *   **metrics:** `["sessions"]`
    *   **date_range_start:** `"28daysAgo"`
    *   **date_range_end:** `"yesterday"`
    *   **dimension_filter:** A filter for 2 high-traffic pages.
*   **Expected Result:** The number of rows should be approximately `(number of days in range) * 2`. For a 28-day period, you should get **~56 rows**. This validates that `date` correctly provides daily granularity.

---

### Test Case 1.3: No Time Dimension (Server-Side Aggregation)

*   **Goal:** Verify that omitting a time dimension correctly triggers the automatic server-side aggregation to get a single total for the entire period.
*   **Query via MCP:**
    *   **Tool:** `get_ga4_data`
    *   **dimensions:** `["pagePath"]`
    *   **metrics:** `["sessions", "totalUsers", "averageSessionDuration"]`
    *   **date_range_start:** `"90daysAgo"`
    *   **date_range_end:** `"yesterday"`
    *   **dimension_filter:** A filter for 5 high-traffic pages.
*   **Expected Result:** The output should contain exactly **5 rows**, one for each page path. The metrics for each row should be the aggregated totals over the entire 90-day period.

---

## Category 2: "Smart Feature" Validation

**Objective:** To verify the unique helper features of this MCP server are functioning as designed.

---

### Test Case 2.1: Large Dataset Warning

*   **Goal:** Ensure the query-size estimation triggers a warning for potentially very large datasets.
*   **Query via MCP:**
    *   **Tool:** `get_ga4_data`
    *   **dimensions:** `["date", "pagePath", "country"]`
    *   **metrics:** `["sessions"]`
    *   **date_range_start:** `"90daysAgo"`
    *   **date_range_end:** `"yesterday"`
*   **Expected Result:** The server should **not** return data. Instead, it should return a `warning` message with an estimated row count (likely > 2500) and suggestions on how to refine the query.

---

### Test Case 2.2: Large Dataset Override

*   **Goal:** Ensure the override flag successfully bypasses the warning from Test Case 2.1.
*   **Query via MCP:**
    *   Use the **exact same query** as Test Case 2.1, but add the override parameter:
    *   **proceed_with_large_dataset:** `True`
*   **Expected Result:** The server **should** now execute the query and return the full (large) dataset.

---

## Category 3: Custom Schema & Filter Logic

**Objective:** To validate that the new dynamic schema works and that complex filters are handled correctly.

---

### Test Case 3.1: Query a Custom Dimension

*   **Goal:** **(Most Important)** Confirm that the server can successfully query a custom dimension specific to your property.
*   **Query via MCP:**
    *   **Tool:** `get_ga4_data`
    *   **dimensions:** `["your_custom_dimension_name"]` (Replace with a real custom dimension from your GA4 setup)
    *   **metrics:** `["sessions"]`
    *   **date_range_start:** `"28daysAgo"`
    *   **date_range_end:** `"yesterday"`
*   **Expected Result:** The query should succeed and return data broken down by your custom dimension. A failure here would indicate an issue with the dynamic schema loading.

---

### Test Case 3.2: Complex `AND`/`OR` Filter

*   **Goal:** Verify that a filter with multiple conditions is applied correctly on the server side.
*   **Query via MCP:**
    *   **Tool:** `get_ga4_data`
    *   **dimensions:** `["country", "deviceCategory"]`
    *   **metrics:** `["sessions"]`
    *   **date_range_start:** `"28daysAgo"`
    *   **date_range_end:** `"yesterday"`
    *   **dimension_filter:** A filter for sessions from either the US or Canada, but only on desktop.
      ```json
      {
        "andGroup": {
          "expressions": [
            {
              "filter": {
                "fieldName": "deviceCategory",
                "stringFilter": { "value": "desktop" }
              }
            },
            {
              "orGroup": {
                "expressions": [
                  {
                    "filter": {
                      "fieldName": "country",
                      "stringFilter": { "value": "United States" }
                    }
                  },
                  {
                    "filter": {
                      "fieldName": "country",
                      "stringFilter": { "value": "Canada" }
                    }
                  }
                ]
              }
            }
          ]
        }
      }
      ```
*   **Expected Result:** The data should only contain rows where `deviceCategory` is "desktop" and `country` is either "United States" or "Canada". This confirms the server is correctly translating the filter structure for the GA4 API.

---

## Category 4: Advanced Integrations & Event Model

**Objective:** To stress test the MCP against other common, but complex, GA4 data models like the event schema and advertising integrations.

---

### Test Case 4.1: Event-Centric Analysis

*   **Goal:** To analyze the parameters of a specific, important event, proving the tool can handle event-scoped dimensions and metrics.
*   **Query via MCP:**
    *   **Tool:** `get_ga4_data`
    *   **dimensions:** `["eventName", "country", "deviceCategory"]`
    *   **metrics:** `["eventCount", "totalUsers"]`
    *   **date_range_start:** `"28daysAgo"`
    *   **date_range_end:** `"yesterday"`
    *   **dimension_filter:** A filter for a key event on your site.
      ```json
      {
        "filter": {
          "fieldName": "eventName",
          "stringFilter": { "value": "your_key_event_name" }
        }
      }
      ```
      *(Note: Replace `your_key_event_name` with a real event, like `generate_lead`, `sign_up`, or `file_download`)*
*   **Expected Result:** The query should succeed and return data filtered to that specific event, showing where and on what devices it's being triggered.

---

### Test Case 4.2: Google Ads Performance Analysis

*   **Goal:** To verify that the tool can correctly pull data from a linked Google Ads account, a common scenario for many businesses.
*   **(Note:** This test is only applicable if you have a Google Ads account linked to your GA4 property.)*
*   **Query via MCP:**
    *   **Tool:** `get_ga4_data`
    *   **dimensions:** `["sessionGoogleAdsCampaignName", "sessionGoogleAdsAdGroupName"]`
    *   **metrics:** `["sessions", "engagementRate", "conversions"]`
    *   **date_range_start:** `"28daysAgo"`
    *   **date_range_end:** `"yesterday"`
    *   **dimension_filter:** A filter to isolate Google Ads traffic.
      ```json
      {
        "andGroup": {
          "expressions": [
            {
              "filter": {
                "fieldName": "sessionSource",
                "stringFilter": { "value": "google" }
              }
            },
            {
              "filter": {
                "fieldName": "sessionMedium",
                "stringFilter": { "value": "cpc" }
              }
            }
          ]
        }
      }
      ```
*   **Expected Result:** The query should succeed and return data showing the performance of your Google Ads campaigns and ad groups, validating that the advertising-related schema is handled correctly.

---

## Category 5: GA4 Admin API Write Tools (v3.2)

**Objective:** Validate the new write tools added in v3.2. Integration tests use the Whitehat test property `443025343`.

---

### Test Case 5.1: Scope Remediation Error (Read-only account)

*   **Goal:** Confirm `ensure_edit_scope` returns a clean remediation error for legacy/read-only accounts.
*   **Precondition:** An OAuth account registered before v3.2 (no `granted_scopes` in its JSON file), OR an account that only granted `analytics.readonly`.
*   **Query:** `ga4_list_key_events(property_id="443025343", account="<legacy_account>")`
*   **Expected:** `{"error": "...", "required_scope": "https://www.googleapis.com/auth/analytics.edit", "remediation": "Re-authorize..."}`. No API call is made.

---

### Test Case 5.2: Scope Upgrade via Re-Authorization (in-process)

*   **Goal:** Confirm that calling the `add_account` MCP tool to re-authorize an account upgrades it from read-only to edit **without restarting the server**, because `save_oauth_credentials` invalidates the in-process credentials cache.
*   **Steps:**
    1. From your MCP client, call `add_account()` → `complete_account_login()` and sign in with the SAME Google account as the legacy one. Grant the "Edit Google Analytics" permission when prompted.
    2. Inspect `~/.config/ga4-mcp/accounts/<email>.json` — `granted_scopes` should include `https://www.googleapis.com/auth/analytics.edit`.
    3. Call `list_accounts()` — the entry for this account should show `"can_edit": true`.
    4. Immediately call `ga4_list_key_events(account="<email>")` — should now succeed, confirming the cache was invalidated and the next call picked up the new refresh token.
*   **Expected:** Cache invalidation is transparent; no server restart required.
*   **CLI variant:** If re-authorizing via `ga4-mcp-add-account` in a terminal instead, the MCP server must be restarted afterwards because the server and CLI run in different OS processes (each with its own in-memory credentials cache).

---

### Test Case 5.3: The Audit Repro (why this release exists)

*   **Goal:** Reproduce the Whitehat scenario end-to-end: detect zero key events, fix it, verify the fix.
*   **Steps:**
    1. `ga4_get_landing_page_summary(property_id="443025343", date_range_start="90daysAgo")` — expect `diagnostics.zero_key_events_warning: true`.
    2. `ga4_create_key_event(event_name="form_submit", property_id="443025343", account="<account_with_edit>")` — expect `{"status": "success", "action": "created"}`.
    3. Re-run the same call immediately — expect `{"status": "success", "action": "existed"}` (idempotency).
    4. Wait 24 hours for data to propagate, then re-run the summary.
*   **Expected:** Post-fix summary shows `totals.key_events > 0` on the property.

---

### Test Case 5.4: Delete Dry-Run

*   **Goal:** Confirm `dry_run=True` resolves the target without deleting.
*   **Steps:**
    1. Pick a stale key event on the test property (or create `test_delete_me` first).
    2. `ga4_delete_key_event(event_name="test_delete_me", dry_run=True, ...)` — expect `{"status": "success", "action": "dry_run", "dry_run": true, "data": {...}}`.
    3. Verify via `ga4_list_key_events` that it still exists.
    4. Re-run without `dry_run` → actual delete.
*   **Expected:** Dry-run returns the would-delete record without removing it; real delete removes it.

---

### Test Case 5.5: Data Stream Update Confirm Guard

*   **Goal:** Verify that `ga4_update_data_stream` refuses to run without `confirm=True`.
*   **Steps:**
    1. `ga4_update_data_stream(stream_id="<id>", display_name="New Name")` — expect `{"error": "confirm=True is required...", "confirm_required": true}`.
    2. Same call with `confirm=True` → expect success with `"action": "updated"` and `"updated_fields": ["display_name"]`.
    3. `ga4_list_data_streams` → verify the new display name.

---

### Test Case 5.6: Custom Dimension USER-Scope Soft Block

*   **Goal:** Verify the `USER` scope requires explicit confirmation.
*   **Steps:**
    1. `ga4_create_custom_dimension(parameter_name="hubspot_contact_id", display_name="HubSpot Contact ID")` — defaults to EVENT scope; expect success with `"action": "created"` (or `"existed"` on re-run).
    2. `ga4_create_custom_dimension(parameter_name="plan_tier", display_name="Plan Tier", scope="USER")` — expect error explaining the cost implications.
    3. Same call with `allow_user_scope=True` — expect success.

---

## Category 6: Aggregation & Audit Helpers (v3.2)

---

### Test Case 6.1: Landing Page Summary Context Safety

*   **Goal:** Confirm the summary returns a context-safe response regardless of property size.
*   **Query:** `ga4_get_landing_page_summary(property_id="443025343", date_range_start="90daysAgo", top_n=200)`
*   **Expected:** Response has at most 200 rows; site-wide `totals` cover the full property (not just top 200); `warnings` and `diagnostics` populated. Response body ≤ ~10k tokens.

---

### Test Case 6.2: Compare Periods Long-Tail Join Warning

*   **Goal:** Verify the `join_warning` flag fires for volatile long-tail dimensions.
*   **Query:**
    ```
    ga4_compare_periods(
        dimensions=["landingPagePlusQueryString"],
        metrics=["sessions", "engagedSessions"],
        period_a_start="56daysAgo", period_a_end="29daysAgo",
        period_b_start="28daysAgo", period_b_end="yesterday",
        sort_by_delta="sessions",
        top_n=50,
    )
    ```
*   **Expected:** Rows sorted by `abs(metrics.sessions.abs_delta)` descending. Any long-tail row with `a==0` or `b==0` triggers `join_warning: true`.

---

### Test Case 6.3: Health Check on Fresh Property

*   **Goal:** Verify `ga4_health_check` surfaces the correct warnings and distinguishes between "no key events *configured*" (urgent) and "no recent key event *activity*" (informational, normal on low-traffic sites).
*   **Query:** `ga4_health_check(property_id="443025343", account="<your_account>")`
*   **Expected response shape:**
    ```json
    {
      "status": "success",
      "has_data": true,
      "has_key_events_configured": false,          // from Admin API list_key_events
      "key_events_configured_count": 0,
      "has_key_event_activity_30d": false,         // from Data API keyEvents metric
      "data_stream_count": 1,
      "last_event_received": "YYYYMMDD",
      "can_edit": true,                            // matches account grants
      "probes": {
        "data_streams_ok": true,
        "key_events_ok": true,
        "data_api_ok": true
      },
      "warnings": [
        "zero_key_events_configured: this property has no key events configured..."
      ]
    }
    ```
*   **Expected warnings:** For the unfixed Whitehat test property, exactly `zero_key_events_configured` should appear (not `zero_key_event_activity_30d` — that's only emitted when key events *are* configured but none fired). After creating `form_submit` in Test Case 5.3 and waiting 24h, re-run — expect `has_key_events_configured: true` and `has_key_event_activity_30d: true`.
*   **Probe-success gating test (permission-denied):** Temporarily use an account that lacks Admin API read access and call `ga4_health_check`. Expected: `probes.data_streams_ok: false` AND `probes.key_events_ok: false`, `warnings` contains exactly ONE `admin_list_permission_denied: ...` entry (the key-events probe is short-circuited because the same permission gates both list calls), and NO `no_data_stream_found`, `zero_key_events_configured`, `list_key_events_failed`, or `list_data_streams_failed` entries — because neither probe succeeded, we cannot claim anything about configuration.
*   **Probe-success gating test (partial pager failure):** Mock the Admin API `list_data_streams` pager to yield one stream then raise a non-PermissionDenied exception. Expected: `probes.data_streams_ok: false`, `data_streams: []`, `data_stream_count: 0`, warning label `list_data_streams_failed_after_1_streams: ...` preserving the partial-progress count for diagnostics.

---

### Test Case 6.4: Traffic-Without-Key-Events Finding (the Whitehat audit repro)

*   **Goal:** Verify `ga4_health_check` surfaces the `traffic_without_key_events` warning that v3.2.0 was built to enable.
*   **Precondition:** A property with `has_data: true` (events recorded in last 30 days) and `has_key_events_configured: false` (no key events registered in Admin). The pre-fix Whitehat test property `443025343` matches this exactly.
*   **Query:** `ga4_health_check(property_id="443025343", account="<edit_capable_account>")`
*   **Expected warnings array** contains BOTH:
    *   `zero_key_events_configured: this property has no key events configured...`
    *   `traffic_without_key_events: the property is receiving events but has no key events configured. Conversions will not be attributed...`
*   **Why both fire:** they're related but distinct findings. `zero_key_events_configured` is the static config state; `traffic_without_key_events` is the dynamic "and you're losing data right now" framing. Both should be visible to the caller.
*   **Post-remediation check:** After `ga4_create_key_event(event_name="form_submit", ...)` in Test Case 5.3, re-running the health check should drop BOTH warnings — `has_key_events_configured: true` and the audit finding warning clears.

---

### Test Case 6.5: Account Validation Early-Exit

*   **Goal:** Verify `ga4_health_check` returns a clean error dict (not a success dict with cascading probe failures) for unknown or malformed account identifiers.
*   **Queries and expected responses:**
    *   `ga4_health_check(account="bogus@example.test")` → `{"error": "No stored credentials for 'bogus@example.test'...", "property_id": ..., "account": "bogus@example.test"}` with NO `warnings`, `probes`, or `status: success`.
    *   `ga4_health_check(account="../etc/passwd")` → `{"error": "Invalid account identifier '../etc/passwd': ...", ...}` — path-traversal rejected by `_safe_account_path`.
    *   `ga4_health_check(account=None)` → runs normally using default credentials; no early bail-out.
*   **Corrupt JSON file:** if an account's token file exists but contains invalid JSON, the early check passes (file exists), then the probe blocks surface the corruption as `credentials_failed:` / `data_api_probe_failed:` warnings — NOT misclassified as "account not found". This is the critical distinction from a naive `except ValueError: return error` approach (JSONDecodeError inherits from ValueError).

---

### Test Case 6.4: `list_properties(name_contains=...)` Filter

*   **Goal:** Verify the filter is applied case-insensitively in Python.
*   **Query:** `list_properties(account="<multi_property_account>", name_contains="whitehat")`
*   **Expected:** Only properties whose display name contains "whitehat" (case-insensitive).

---

## Category 7: Screaming Frog CSV Bridge (v3.2)

---

### Test Case 7.1: Partial Load Semantics

*   **Goal:** Verify that broken files are reported but do not abort the load.
*   **Setup:** Create a folder with `analytics_all.csv` (valid), `analytics_orphan.csv` (valid), and `analytics_broken.csv` (intentionally malformed — truncated quote or invalid UTF-8).
*   **Query:** `ga4_load_sf_analytics_csvs(sf_export_path="<folder>")`
*   **Expected:** `{"status": "success", "session_id": "sf-...", "datasets": {"all": n, "orphan": n}, "loaded_partial": [{"file": "analytics_broken.csv", "reason": "..."}]}`.

---

### Test Case 7.2: Query Filter Operators

*   **Goal:** Exercise each supported filter operator.
*   **Queries:**
    1. `ga4_query_sf_analytics(session_id, dataset="all", filter={"address": {"contains": "/blog"}})`
    2. `ga4_query_sf_analytics(session_id, dataset="all", filter={"sessions": {"gt": 100}})`
    3. `ga4_query_sf_analytics(session_id, dataset="all", sort_by="sessions", sort_desc=True, limit=10)`
*   **Expected:** Each returns filtered/sorted rows with `total_matched`, `returned`, and `limit`.

---

### Test Case 7.3: Invalid Operator

*   **Goal:** Verify unsupported filter operators are rejected up front.
*   **Query:** `ga4_query_sf_analytics(session_id, dataset="all", filter={"sessions": {"between": [100, 200]}})`
*   **Expected:** `{"error": "Unsupported filter operator 'between'..."}`. No row scan performed.

---

### Test Case 7.4: Header-only CSV preserved as empty queryable dataset

*   **Goal:** Verify that a legitimately empty CSV (header row only, zero data rows — e.g. `analytics_orphan_urls.csv` on a site with no orphan pages) is stored as an empty queryable dataset, NOT dropped from the session.
*   **Setup:** Create a folder with `analytics_orphan_urls.csv` containing only a header line (e.g., `address,title\n`) and one other file with real data.
*   **Steps:**
    1. `ga4_load_sf_analytics_csvs(sf_export_path="<folder>")` → `status: success`, `datasets` contains BOTH files (the empty one shows `row_counts["orphan_urls"] == 0`).
    2. `ga4_query_sf_analytics(session_id, dataset="orphan_urls", limit=100)` → `{"status": "success", "rows": [], "total_matched": 0, "returned": 0}` — NOT "dataset not found".
*   **All-empty edge case:** If every file in the folder is header-only, the load still returns `status: success` with empty datasets, NOT the "Failed to load any analytics_*.csv files" error.

---

### Test Case 7.5: sort_desc honored for text columns

*   **Goal:** Verify that `sort_desc=True` produces descending order for both numeric AND text columns (previously silently ignored for text).
*   **Setup:** A CSV with columns `address` (text) and `sessions` (numeric), populated with 5+ rows of mixed data.
*   **Queries:**
    1. `ga4_query_sf_analytics(session_id, dataset="all", sort_by="address", sort_desc=True)` → rows in alphabetical Z→A order
    2. `ga4_query_sf_analytics(session_id, dataset="all", sort_by="address", sort_desc=False)` → rows in alphabetical A→Z order
    3. `ga4_query_sf_analytics(session_id, dataset="all", sort_by="sessions", sort_desc=True)` → rows in numeric high→low order, blanks/non-numeric at the end
*   **Expected:** `sort_desc` applies uniformly; non-numeric values (blank, NaN, unparseable) always group at the end regardless of direction.

---

## Category 8: Round 3 Fixes (Admin credential error handling)

### Test Case 8.1: Corrupt token file with write tool

*   **Goal:** Verify that a write tool on an account with a corrupted token file surfaces a "corrupt file" error at the `ensure_edit_scope` level, NOT the misleading "legacy registration" error that `get_granted_scopes` would produce by swallowing `JSONDecodeError`.
*   **Setup:**
    1. Register a valid OAuth account via `add_account`.
    2. Manually corrupt the JSON file: overwrite `~/.config/ga4-mcp/accounts/<email>.json` with invalid JSON (e.g., `{"email": "broken`).
*   **Query:** `ga4_create_key_event(event_name="form_submit", account="<corrupted_email>")`
*   **Expected:** `{"error": "Credential file for '<email>' is corrupt or unreadable: ...", "exception_type": "JSONDecodeError", ...}`. NOT `"Account '<email>' has no recorded scope grants (legacy registration or unknown account)..."`.
*   **Recovery:** Re-run `ga4-mcp-add-account` (or the `add_account` MCP tool) to overwrite the corrupt file with a valid re-authorized token.

---

### Test Case 8.2: Revoked refresh token with Admin tool

*   **Goal:** Verify that a `RefreshError` from google-auth (e.g., refresh token revoked in the user's Google account security settings) is caught by `_admin_client` and returned as a normalized error dict — NOT propagated as an uncaught tool failure.
*   **Setup:**
    1. Register a valid OAuth account.
    2. In Google Account → Security → Third-party apps with account access, revoke the GA4 MCP app's access.
*   **Query:** `ga4_list_key_events(account="<revoked_email>")` (or any other admin tool)
*   **Expected:** `{"error": "Refresh token for '<email>' was rejected by Google: ...", "exception_type": "RefreshError", ...}`. No uncaught exception.

---

### Test Case 8.3: Unknown account with Admin tool

*   **Goal:** Verify the account-exists pre-check catches typo'd account names before any network call.
*   **Query:** `ga4_list_key_events(account="typo@example.test")`
*   **Expected:** `{"error": "No stored credentials for 'typo@example.test'. Use the add_account MCP tool or run `ga4-mcp-add-account` to register this account.", ...}`. No HTTP requests made.

---

### Test Case 8.4: Path-traversal account identifier

*   **Goal:** Verify `_safe_account_path` rejects path-traversal attempts with a clear error.
*   **Query:** `ga4_list_key_events(account="../../etc/passwd")`
*   **Expected:** `{"error": "Invalid account identifier '../../etc/passwd': ...", ...}`.

---

## Category 9: Round 4 Fixes (Aggregation tools — nullable semantics)

### Test Case 9.1: Landing page summary — `(not set)` already in top_n (no extra API call)

*   **Goal:** When `(not set)` appears in the main response's top_n rows, `ga4_get_landing_page_summary` should compute `high_not_set_pct` from the existing data WITHOUT issuing a targeted probe.
*   **Steps:**
    1. Mock the main `run_report` to return top_n rows including one for `landingPagePlusQueryString == "(not set)"` with e.g. 1500 sessions, plus a totals row with e.g. 10000 sessions.
    2. Mock the `(not set)` targeted probe to raise `AssertionError` (to catch unwanted invocation).
    3. Call `ga4_get_landing_page_summary(...)`.
*   **Expected:**
    *   `totals.high_not_set_pct == 0.15`
    *   `totals.high_not_set_pct_source == "top_rows"`
    *   `probes.not_set_probe_ok == True`
    *   `diagnostics.high_not_set_pct_warning == True` (> 5%)
    *   Targeted probe was NOT called.

### Test Case 9.2: Landing page summary — `(not set)` outside top_n (targeted probe)

*   **Goal:** When `(not set)` is absent from the top_n rows, the function should issue the targeted filtered query.
*   **Setup:** Main response returns top_n rows without any `(not set)` entry, totals row has 10000 sessions. Targeted probe returns 1 row with 2000 sessions.
*   **Expected:**
    *   `totals.high_not_set_pct == 0.2`
    *   `totals.high_not_set_pct_source == "filtered_probe"`
    *   `probes.not_set_probe_ok == True`
    *   Targeted probe WAS called exactly once.

### Test Case 9.3: Landing page summary — not-set probe fails gracefully

*   **Goal:** When the targeted probe raises, `high_not_set_pct` must be `None` (not 0.0), the warning must NOT fire, and `probes.not_set_probe_ok` must be `False`.
*   **Setup:** Main response has no `(not set)` rows; targeted probe raises an exception.
*   **Expected:**
    *   `totals.high_not_set_pct is None`
    *   `totals.high_not_set_pct_source == "unavailable"`
    *   `probes.not_set_probe_ok == False`
    *   `diagnostics.high_not_set_pct_warning == False`
    *   `warnings` contains `not_set_probe_failed: ...`

### Test Case 9.4: Landing page summary — empty property does not false-fire warnings

*   **Goal:** On an empty property (zero sessions in the date range), neither `low_engagement_warning` nor `zero_key_events_warning` should fire. Previously both would fire because `engagement_rate` defaulted to 0.0 and `keyEvents` defaulted to 0.
*   **Setup:** Mock the main call to return zero rows and a totals row with all metrics = 0 (or None).
*   **Expected:**
    *   `totals.engagement_rate is None` (not 0.0)
    *   `diagnostics.zero_key_events_warning == False`
    *   `diagnostics.low_engagement_warning == False`
    *   `diagnostics.high_not_set_pct_warning == False`
    *   No misleading remediation warnings in the response.

### Test Case 9.5: Compare periods — totals_delta via server-side TOTAL

*   **Goal:** Verify `totals_delta["engagementRate"]` uses the GA4 report-wide totals row, NOT a Python sum of per-row rates.
*   **Setup:** Mock period A to return a totals row with `engagementRate=0.5`, `sessions=1000`. Mock period B to return a totals row with `engagementRate=0.6`, `sessions=1200`. The per-row values can be anything.
*   **Expected:**
    *   `totals_delta["engagementRate"] == {"a": 0.5, "b": 0.6, "abs_delta": ~0.1, "pct_delta": 0.2}`
    *   `totals_delta["sessions"] == {"a": 1000, "b": 1200, "abs_delta": 200, "pct_delta": 0.2}`
    *   `metadata.totals_scope == "full_filtered_report"`
    *   `probes.period_a_totals_available == True`
    *   `probes.period_b_totals_available == True`

### Test Case 9.6: Compare periods — one period totals unavailable

*   **Goal:** When GA4 returns no totals row for one period (e.g. empty data), per-metric deltas are `None` for the affected metrics and `probes.period_*_totals_available` reflects which one.
*   **Setup:** Period A returns a normal totals row. Period B returns `response.totals = []` (no totals row).
*   **Expected:**
    *   `totals_delta["sessions"] == {"a": <number>, "b": None, "abs_delta": None, "pct_delta": None}`
    *   `probes.period_b_totals_available == False`

### Test Case 9.7: Health check — all probes succeed (status=success)

*   **Goal:** When all three probes (data streams, key events, Data API) succeed, the status is `"success"` and all fields have definitive values.
*   **Expected:**
    *   `status == "success"`
    *   `has_data`, `has_key_events_configured`, `has_key_event_activity_30d` are definitive booleans (not None)
    *   `key_events_configured_count` and `data_stream_count` are definitive integers (not None)
    *   `probes` sub-dict has all three flags as `True`.

### Test Case 9.8: Health check — partial probe failure (status=partial_success)

*   **Goal:** When one probe fails but others succeed, status is `"partial_success"` and the fields from the failed probe are `None` (not `False` / `0`).
*   **Setup:** Mock `list_data_streams` to raise, Data API and `list_key_events` succeed.
*   **Expected:**
    *   `status == "partial_success"`
    *   `data_stream_count is None` (was `0` before Round 4 — the fix)
    *   `data_streams == []`
    *   `has_data`, `has_key_events_configured` are definitive (their probes succeeded)
    *   `probes.data_streams_ok == False`, `probes.key_events_ok == True`, `probes.data_api_ok == True`

### Test Case 9.9: Health check — all probes fail (error dict)

*   **Goal:** When no probe succeeds (e.g. no credentials, no access to property), the response is an error dict with `probes` and `warnings` included — NOT a success dict with all-False fields.
*   **Setup:** Mock `resolve_credentials` to raise, which cascades into all three probes failing.
*   **Expected:**
    *   Response has `error` key with a message about "all probes failed"
    *   Response has `probes` sub-dict showing all three as `False`
    *   Response has `warnings` list with the specific failure modes
    *   NO `status: "success"` claim
    *   NO `has_data: False` etc. (those are nullable and absent from error dicts)

---

## Category 10: Round 5 Fixes (Nullable Semantics Sweep)

### Test Case 10.1: `totals.form_events` is None on query failure

*   **Goal:** When the form events query raises an exception, `totals.form_events` must be `None` (not `{form_submit: 0, form_start: 0}`), and `probes.form_events_probe_ok` must be `False`.
*   **Setup:** Mock the main `run_report` to return normal landing-page data with totals. Mock the second `run_report` call (form events query) to raise an exception.
*   **Expected:**
    *   `totals.form_events is None` (was `{form_submit: 0, form_start: 0}` before Round 5 — the bug)
    *   `probes.form_events_probe_ok is False`
    *   `warnings` list contains `form_events_query_failed: ...`
    *   Rest of the response shape unchanged (top landing pages, totals, etc.)

### Test Case 10.2: `totals.form_events` partial success (some events fired, others didn't)

*   **Goal:** When the form events query succeeds and one event fired but the other didn't, the response contains measured counts for the firing event AND a measured-zero for the non-firing event. GA4 omits all-zero rows from the response by default, so this exercises the "fill missing events with 0" branch.
*   **Setup:** Mock the form events query to return one row: `eventName=form_submit, eventCount=42`. The `form_start` event is NOT in the response (it didn't fire).
*   **Expected:**
    *   `totals.form_events == {"form_submit": 42, "form_start": 0}`
    *   `probes.form_events_probe_ok is True`
    *   No `form_events_query_failed` warning

### Test Case 10.3: `totals.form_events` all-zero on successful query with empty period

*   **Goal:** When the form events query succeeds but returns zero rows (empty period or no events fired at all), the response reports every form event as measured-zero, not None.
*   **Setup:** Mock the form events query to return an empty rows list.
*   **Expected:**
    *   `totals.form_events == {"form_submit": 0, "form_start": 0}`
    *   `probes.form_events_probe_ok is True` (the probe succeeded, the property just has no form events)
    *   No warnings related to form events

### Test Case 10.4: `median_sessions` / `median_engagement_rate` are None on empty `shaped_rows`

*   **Goal:** When the main landing-page query returns zero rows (empty property / empty date range), both median fields must be `None`, not `0`.
*   **Setup:** Mock the main query to return an empty rows list. Totals row may or may not be present (test both).
*   **Expected:**
    *   `totals.median_sessions is None`
    *   `totals.median_engagement_rate is None`
    *   `rot_flag` does not appear in any row (there are no rows)

### Test Case 10.5: `median_sessions` is float for even-sized `shaped_rows` (per open-gem)

*   **Goal:** Verify that `median_sessions` can be a float, not just an int. `statistics.median([100, 200])` returns `150.0` (float) because of the two-element even-size average.
*   **Setup:** Mock the main query to return exactly 2 rows with `sessions` values 100 and 200.
*   **Expected:**
    *   `totals.median_sessions == 150.0`
    *   `isinstance(totals.median_sessions, float) is True`
    *   Any caller/display code that assumed `int` will need to handle `float` too (this is a pre-existing semantic, just now uncovered by the test).

### Test Case 10.6: `ga4_health_check.data_streams` is None on probe failure

*   **Goal:** When `list_data_streams` raises, BOTH `data_stream_count` AND `data_streams` must be `None` — not `None` and `[]` which was the Round 4 → Round 5 inconsistency.
*   **Setup:** Mock `list_data_streams` to raise `RuntimeError`. Mock `list_key_events` and the Data API probe to succeed so the overall status is `partial_success`.
*   **Expected:**
    *   `status == "partial_success"`
    *   `data_stream_count is None`
    *   `data_streams is None` (was `[]` before Round 5 — the bug)
    *   `probes.data_streams_ok is False`

### Test Case 10.7: `ga4_health_check.data_streams` is `[]` for real measured-zero

*   **Goal:** Regression — when the probe succeeds and the property genuinely has zero data streams, `data_streams` is an empty list `[]` (real measured-zero), NOT `None`.
*   **Setup:** Mock `list_data_streams` to return an empty iterator cleanly (no exception, zero items).
*   **Expected:**
    *   `status == "success"` (all probes OK, assuming the other probes pass)
    *   `data_stream_count == 0`
    *   `data_streams == []` (NOT `None` — the probe measured this)
    *   `probes.data_streams_ok is True`

### Test Case 10.8: Round 4 regression — no field names changed

*   **Goal:** Verify that Round 5 did NOT rename any existing `probes` sub-dict keys (the N2 rename was dropped after open-gem critique).
*   **Checks (via grep or inspection of live responses):**
    *   `ga4_get_landing_page_summary.probes` still uses `totals_available`, `not_set_probe_ok` (plus the new `form_events_probe_ok` from M1)
    *   `ga4_compare_periods.probes` still uses `period_a_totals_available`, `period_b_totals_available`
    *   `ga4_health_check.probes` still uses `data_streams_ok`, `key_events_ok`, `data_api_ok`
    *   Round 4 behavioral tests pass unchanged (no test rewrites required)

---

## Category 11: Multi-Account & Multi-Property Discoverability (v3.3.0)

These scenarios verify the agent-facing fix for the "default property trap" — agents seeing a configured default and falsely concluding they have no access to other properties or other registered accounts. The selector is `(account, property_id)` as a pair.

### Test Case 11.1: `get_ga4_data` defaulted property — `property_used` + `notice` emitted

*   **Goal:** When `GA4_PROPERTY_ID` is set and the agent calls `get_ga4_data` with no `property_id`, the response surfaces which property was actually used and includes a `notice` pointing the agent at `list_properties()`.
*   **Setup:** Set `GA4_PROPERTY_ID=<some-numeric-id>`, do not pass `property_id` to the tool.
*   **Expected:**
    *   `response.property_used.id == <some-numeric-id>`
    *   `response.property_used.was_explicit is False`
    *   `response.property_used.account == "default credentials"`
    *   `response.property_used.account_parameter is None`
    *   `response.notice` exists and mentions `list_properties()` and `account=`

### Test Case 11.2: `get_ga4_data` explicit property — `was_explicit: True`, no notice

*   **Goal:** Passing `property_id` explicitly suppresses the notice.
*   **Setup:** With `GA4_PROPERTY_ID` set to property A, call `get_ga4_data(property_id="<property-B>")`.
*   **Expected:**
    *   `response.property_used.id == "<property-B>"`
    *   `response.property_used.was_explicit is True`
    *   `"notice" not in response`

### Test Case 11.3: No default + no explicit — actionable error

*   **Goal:** When `GA4_PROPERTY_ID` is unset and the agent calls a tool with no `property_id`, the error message names `list_accounts()`, `list_properties(account="...")`, and the credential pairing rule.
*   **Setup:** Unset `GA4_PROPERTY_ID`, call any tool with `property_id=None`.
*   **Expected:**
    *   `response.error` mentions `list_accounts()`, `list_properties()`, and `account="..."`.
    *   `response.error` does not just say the generic "No property_id provided".

### Test Case 11.4: `list_accounts()` shape — `usage` + per-entry `account_parameter`

*   **Goal:** Agents reading `list_accounts()` see how to use what they got.
*   **Setup:** Have at least one OAuth account registered AND default credentials available. Call `list_accounts()`.
*   **Expected:**
    *   `response.usage` is a non-empty string mentioning `list_properties(account=...)` and the credential-scoping rule.
    *   The default-credentials entry has `email == "default credentials"` AND `account_parameter is None`.
    *   Each OAuth entry has `account_parameter == <email>` (string, never null).

### Test Case 11.5: `list_properties()` (no account) — `usage` says "not global", `other_accounts_available` populated

*   **Goal:** Agents reading `list_properties()` with no `account` argument see that the result is scoped to default credentials only AND learn other accounts exist.
*   **Setup:** Default credentials configured + 2 OAuth accounts registered. Call `list_properties()` with no `account`.
*   **Expected:**
    *   `response.usage` includes the phrase "not include properties accessible only through registered OAuth accounts" (or equivalent — the literal string "not global" or "default credentials only" suffices).
    *   `response.other_accounts_available` is a list with both registered OAuth emails.
    *   `response.account_parameter is None`.
    *   `response.using_account == "default credentials"` (preserved from v3.2.0).

### Test Case 11.6: `list_properties(account="alice@example.com")` — `usage` mentions both `property_id` and the same account

*   **Goal:** Agents reading per-account `list_properties` learn the pairing rule for next-call scoping.
*   **Setup:** Register `alice@example.com`, call `list_properties(account="alice@example.com")`.
*   **Expected:**
    *   `response.usage` mentions both `property_id` AND `account="alice@example.com"`.
    *   `response.account_parameter == "alice@example.com"`.
    *   `response.using_account == "alice@example.com"`.
    *   `response.other_accounts_available` does NOT contain `alice@example.com` (it should list the other OAuth accounts).

### Test Case 11.7: Per-account property query end-to-end

*   **Goal:** A property discovered under one account remains queryable when the same account is passed back.
*   **Setup:** Find a property `P` under account `alice@example.com` via `list_properties(account="alice@example.com")`. Then call `get_ga4_data(property_id=P, account="alice@example.com")`.
*   **Expected:**
    *   Successful data response.
    *   `response.property_used.account == "alice@example.com"`.
    *   `response.property_used.account_parameter == "alice@example.com"`.
    *   `response.property_used.account_was_explicit is True`.

### Test Case 11.8: Per-account property without account fails (or returns unrelated default-credentials data)

*   **Goal:** Demonstrate that omitting `account` for an OAuth-only property does NOT silently succeed — exercising the trap the fix addresses.
*   **Setup:** Take property `P` accessible only via `alice@example.com`. Call `get_ga4_data(property_id=P)` with no `account`.
*   **Expected (non-deterministic, but one of):**
    *   GA4 API permission error from the underlying client (because default credentials don't have access to `P`).
    *   `response.property_used.account_was_explicit is False` so the agent can see it was using default credentials and re-issue with `account="alice@example.com"`.

### Test Case 11.9: No default credentials + OAuth accounts exist — actionable error from `list_properties()`

*   **Goal:** Don't let the underlying ADC error bubble up. Don't silently fall back to "the first OAuth account".
*   **Setup:** Unset `GOOGLE_APPLICATION_CREDENTIALS`. Register at least one OAuth account. Call `list_properties()` with no `account`.
*   **Expected:**
    *   `response.error` mentions "list_properties() without account uses default credentials".
    *   `response.available_accounts` is a list of all registered OAuth emails.
    *   `response.usage` instructs to call `list_properties(account="<email>")`.
    *   Tool does NOT silently use one of the OAuth accounts.

### Test Case 11.10: Write-tool default-property notice — `ga4_create_key_event`

*   **Goal:** Silent default-property writes are the highest-stakes failure mode. Verify the notice fires.
*   **Setup:** Set `GA4_PROPERTY_ID`, call `ga4_create_key_event(event_name="form_submit")` with no `property_id`.
*   **Expected:**
    *   On success/created/existed, `response.property_used.was_explicit is False` AND `response.notice` is present.

### Test Case 11.11: `ga4_update_data_stream(confirm=False)` early-return includes `property_used`

*   **Goal:** When the tool refuses to execute without `confirm=True`, the agent prompting the user must still see WHICH property the change would land on.
*   **Setup:** Call `ga4_update_data_stream(stream_id="123", display_name="x", confirm=False)` with no `property_id` while `GA4_PROPERTY_ID` is set.
*   **Expected:**
    *   `response.error` mentions "confirm=True is required".
    *   `response.confirm_required is True`.
    *   `response.property_used` is present and points to the default property.
    *   `response.notice` is present (was_explicit was False).

### Test Case 11.12: `ga4_create_custom_dimension(scope="USER", allow_user_scope=False)` soft-block includes `property_used`

*   **Goal:** Same reasoning as 11.11 — confirm flow must show target property.
*   **Setup:** Call `ga4_create_custom_dimension(parameter_name="x", display_name="X", scope="USER", allow_user_scope=False)` with no `property_id` while `GA4_PROPERTY_ID` is set.
*   **Expected:**
    *   `response.error` mentions USER-scope cost implications.
    *   `response.property_used` is present.
    *   `response.notice` is present.

### Test Case 11.13: `get_property_schema` does not mutate `SCHEMA_CACHE`

*   **Goal:** Adding `property_used` to the response must not pollute the cached schema for future calls.
*   **Setup:** Call `get_property_schema(property_id="<X>")` once with `was_explicit=True` (returns property_used.was_explicit=True). Then call it again with no `property_id` while `GA4_PROPERTY_ID="<X>"` (returns property_used.was_explicit=False).
*   **Expected:**
    *   First response: `property_used.was_explicit is True`.
    *   Second response: `property_used.was_explicit is False` (NOT True — would be the bug if the cache had been mutated).
    *   Inspect `ga4_mcp.tools.metadata.SCHEMA_CACHE[(<X>, "__default__")]` directly: it must NOT contain a `property_used` key.

### Test Case 11.14: `_resolve_pid_or_error` rejects whitespace as explicit

*   **Goal:** `bool("   ")` is True. Verify the resolver strips before computing `was_explicit`.
*   **Setup (unit test):** Call `_resolve_pid_or_error("   ")` directly.
*   **Expected:**
    *   Returns `(None, False, {"error": ...})` — `was_explicit` must be `False`, not `True`.
    *   The error message references `list_accounts()`, `list_properties()`, and `account=`.

### Test Case 11.15: Backward compat — flat-dict tools unchanged

*   **Goal:** `get_dimensions_by_category` and `get_metrics_by_category` return flat `{name: description}` dicts — no `property_used` injected, no shape change.
*   **Setup:** Call each with a known-good category.
*   **Expected:**
    *   Response is a dict whose values are all strings (descriptions).
    *   No `property_used` key in the response.
    *   No `notice` key in the response.
    *   `for name, desc in response.items(): assert isinstance(desc, str)` succeeds.

### Test Case 11.16: Backward compat — `list_properties.default_property_id` unchanged

*   **Goal:** Existing scripts that read `default_property_id` still work.
*   **Setup:** Set `GA4_PROPERTY_ID`, call `list_properties()`.
*   **Expected:**
    *   `response.default_property_id == "<set-value>"` exactly.
    *   `response.using_account`, `response.name_contains`, `response.total` are present and behave as in v3.2.0.
