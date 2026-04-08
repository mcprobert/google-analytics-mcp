# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.2.0] - 2026-04-08

### Added
- **GA4 Admin API write tools** (new `ga4_mcp/tools/admin.py`). Audits can now fix measurement-layer issues in-band instead of requiring a human to click through the GA4 UI:
  - `ga4_list_key_events`, `ga4_create_key_event` (idempotent, paginated scan), `ga4_delete_key_event` (supports `dry_run=True`)
  - `ga4_list_data_streams`, `ga4_update_data_stream` (requires `confirm=True`; updates `display_name` and `web_stream_data.default_uri`)
  - `ga4_list_custom_dimensions`, `ga4_create_custom_dimension` (idempotent; `USER` scope soft-blocked behind `allow_user_scope=True`)
- **`ga4_get_landing_page_summary`** — context-safe audit summary with server-side totals, rot/engagement diagnostics, zero-key-events warning, and configurable `form_event_names` (defaults to `["form_submit","form_start"]`).
- **`ga4_compare_periods`** — two-period delta comparison with outer-join on dimension tuples and `join_warning` flag for long-tail truncation.
- **Screaming Frog CSV bridge** (new `ga4_mcp/tools/sf_bridge.py`): `ga4_load_sf_analytics_csvs` + `ga4_query_sf_analytics`. Thread-safe in-memory session store, stdlib-only, no network I/O.
- **`ga4_health_check`** — one-shot audit diagnostic. Returns `has_key_events_configured` (from Admin API `list_key_events` — the *is it set up?* question), `has_key_event_activity_30d` (from Data API — the *is anything firing?* question), `key_events_configured_count`, `data_stream_count`, `last_event_received`, `can_edit`, and a `probes` dict indicating which API probes succeeded. Warnings are only emitted when the corresponding probe completed cleanly (no false `no_data_stream_found` after a permission error; no false `zero_key_events_configured` remediation on low-traffic properties).
- **`list_properties(name_contains=...)`** — case-insensitive substring filter for accounts with many properties.
- **`list_accounts`** now reports `granted_scopes` and `can_edit` per account.
- New `analytics.edit` OAuth scope requested for new registrations; `include_granted_scopes=true` set on the auth URL so re-auth upgrades read-only accounts without losing previously granted scopes (requires signing in with the same Google account).
- New `ensure_edit_scope(account)` helper in `auth.py`. Every write tool gates on it and returns a clean remediation message if the account lacks `analytics.edit`. Legacy account JSON files (schema v1, no `granted_scopes` field) fail closed — `ensure_edit_scope` treats them as read-only and directs the user to re-authorize. Corrupt token files are surfaced as a dedicated "credential file is corrupt" error rather than the generic "legacy registration" message.
- **Nullable-semantics response shape** for the aggregation and diagnostic tools. Fields that cannot be measured return `None` rather than a misleading `0` / `False` / `[]`:
  - `ga4_get_landing_page_summary` — `totals.sessions`, `totals.engaged_sessions`, `totals.engagement_rate`, `totals.key_events`, `totals.total_users`, `totals.form_events`, `totals.high_not_set_pct`, and the median fields are `None` when the underlying GA4 totals row or sub-probe was unavailable. A `totals.high_not_set_pct_source` field and a top-level `probes` sub-dict (`totals_available`, `not_set_probe_ok`, `form_events_probe_ok`) tell the caller which measurements succeeded.
  - `ga4_compare_periods` — `totals_delta` now comes from GA4's server-side `MetricAggregation.TOTAL` rather than a Python sum across top-N rows, which makes the totals valid for ratio and duration metrics (`engagementRate`, `averageSessionDuration`, `bounceRate`, etc.). Per-metric deltas are `None` when a period's totals row is unavailable; new `probes.period_a_totals_available` / `probes.period_b_totals_available` flags distinguish. Metadata includes `totals_scope: "full_filtered_report"` so callers do not expect `sum(rows[m]) == totals_delta[m]`.
  - `ga4_health_check` — tri-state `status` field: `"success"` when all probes succeed, `"partial_success"` when some fail, or an error dict (no `status: success` claim) when all probes fail. Probe-dependent fields (`has_data`, `has_key_events_configured`, `key_events_configured_count`, `has_key_event_activity_30d`, `data_stream_count`, `data_streams`) are `None` when the underlying probe failed — distinguishing "we could not measure this" from "we measured and it is zero". `can_edit` remains a definitive boolean because it is read from stored OAuth scopes. The `last_event_received: None` ambiguity (probe failed vs. no events recorded) is now documented in the tool docstring.
- **Audit diagnostics** — new warnings that fire on real GA4 audit findings: `traffic_without_key_events` (the Whitehat finding that prompted the release), `stale_key_event_activity` (key events deleted but historical counts still in the 30-day window), `data_streams_configured_but_silent`, `zero_key_event_activity_30d`. Empty-property warnings (`low_engagement_warning`, `zero_key_events_warning`) no longer false-fire when a property has zero sessions in the selected date range.
- **Screaming Frog CSV bridge correctness** — header-only CSVs (e.g. `analytics_orphan_urls.csv` on a clean site) are preserved as empty queryable datasets instead of being dropped. `sort_desc` is honored uniformly for numeric AND text columns via a two-phase partition sort (non-numeric rows always group at the end regardless of direction). `_coerce_number` filters NaN values that would otherwise pollute the numeric sort group with non-deterministic order. Mid-file session-cap truncation is reported in `loaded_partial` for the current file, not just later files.
- **Admin API error normalization** — all seven Admin write tools now route credential-resolution failures through a new `AdminClientError` exception that produces distinct actionable error messages for each failure mode (unknown account, corrupt token file, revoked refresh token, transport error, missing default credentials). Programming errors (KeyError, AttributeError) still propagate uncaught so they surface in tests.

### Changed
- `save_oauth_credentials` accepts and persists a `granted_scopes` list (schema version 2). Missing field = legacy account, treated as read-only by `ensure_edit_scope`. Also invalidates the in-process credentials cache entry for the email so re-auth inside the same server process takes effect on the very next call. Credential files are written atomically via temp-file + `os.replace`, so readers never observe a partially-written file on concurrent save.
- `complete_oauth_flow` captures scopes from the token-exchange response's `scope` field (authoritative).
- `get_oauth_credentials` passes the persisted `granted_scopes` (or `None` for legacy accounts) to the `Credentials` constructor, rather than the full `SCOPES` superset. This fixes a critical regression where legacy v3.1.0 read-only refresh tokens would have been rejected by Google as `invalid_scope` on every refresh under v3.2.0.
- `ga4-mcp-add-account` CLI captures `creds.scopes` and writes them to the account file (best-effort; in practice mirrors the token-exchange `scope` field).

### Migration notes
- Existing users must re-run `ga4-mcp-add-account` (or call the `add_account` MCP tool) to grant `analytics.edit` before using any write tool. Read-only behavior is unchanged — existing accounts continue to work with the existing read-only tools without any action.
- When re-authorizing via the terminal CLI (`ga4-mcp-add-account`) while the MCP server is running in a separate process, you must restart the MCP server for the new credentials to take effect. When re-authorizing via the `add_account` MCP tool (in-process), no restart is required — the credentials cache is invalidated automatically.

## [3.1.0] - 2026-03-26

### Added
- **Zero-config OAuth login**: Embedded OAuth client credentials so users can sign in with their Google account without needing their own Google Cloud project or client secrets file
- `ga4_mcp/default_client.py` module holding the shared OAuth client configuration

### Changed
- `add_account` MCP tool no longer requires `GA4_MCP_OAUTH_CLIENT_SECRETS` environment variable
- `ga4-mcp-add-account` CLI no longer requires `--client-secrets` flag (now optional override)
- `_get_oauth_client_config()` falls back to embedded credentials when env var is not set
- Simplified server startup messaging for first-run experience
- `GA4_MCP_OAUTH_CLIENT_SECRETS` removed from `server.json` environment variables (still supported as override)

## [3.0.1] - 2026-03-25

### Fixed
- **Security:** Credential files now created with 0o600 permissions, accounts directory with 0o700
- **Security:** Path traversal prevention on email-based file operations via `_safe_account_path()` validation
- **Security:** HTML-escape OAuth error callback responses to prevent XSS
- **Security:** OAuth state parameter validation to prevent CSRF on callback
- **Security:** HTTP request timeouts on OAuth token exchange and userinfo calls
- **Concurrency:** Replaced shared class-level OAuth handler state with per-flow `_OAuthServer` instance
- **Concurrency:** Added flow lock to prevent concurrent OAuth flow corruption
- **Concurrency:** Server socket always cleaned up via `server_close()` in finally block
- **Correctness:** Schema cache now keyed by `(property_id, account)` to prevent cross-account stale data
- **Correctness:** Mutable default arguments in `get_ga4_data` replaced with `None` defaults
- **Correctness:** Property ID validation rejects non-numeric IDs with clear error message
- **Correctness:** `list_accounts` detects application default credentials instead of hardcoding service account
- **Correctness:** Estimation failure now fails closed — returns warning instead of proceeding to full query
- **Correctness:** Filter dimension names validated against property schema before API call
- **Correctness:** Defensive handling of `e.details()` in error handler to prevent secondary crashes
- **Correctness:** Removed dead `len(dimensions) == 0` check from `_should_aggregate`
- **Packaging:** Created `ga4_mcp/tools/__init__.py` for reliable non-editable installs

## [3.0.0] - 2026-03-25

### Added
- **Multi-property support**: All tools now accept an optional `property_id` parameter to query any accessible GA4 property without restarting the server
- **Multi-account OAuth**: Register multiple Google accounts via interactive OAuth flow; use the `account` parameter on any tool to switch between them
- **New tools**:
  - `list_accounts` — show available service account and registered OAuth accounts
  - `add_account` / `complete_account_login` — two-step interactive OAuth registration, usable from Claude Desktop
  - `remove_registered_account` — remove a previously registered OAuth account
  - `list_properties` — discover all GA4 properties accessible by a given account
- **Per-property schema caching**: schemas are fetched on first use per property and cached for the session
- `google-analytics-admin` and `google-auth-oauthlib` added as dependencies
- `ga4-mcp-add-account` CLI entry point for terminal-based account registration
- `GA4_MCP_OAUTH_CLIENT_SECRETS` environment variable for OAuth client configuration

### Changed
- `GA4_PROPERTY_ID` is now optional (used as default; can be overridden per tool call)
- `GOOGLE_APPLICATION_CREDENTIALS` is now optional when OAuth accounts are registered
- Server startup no longer exits fatally if the default property schema fails to load
- Schema caching moved from module-level globals to a shared `SCHEMA_CACHE` dict in metadata module

### Removed
- Single-property-only limitation — all tools now support cross-property queries

## [2.0.1] - 2026-03-12

### Changed
- Aligned the README, PyPI long description, and config templates around the supported launch commands: `ga4-mcp-server` and `python -m ga4_mcp`
- Updated MCP registry metadata to use the current package version, accurate environment variables, and a production-ready description
- Standardized the project license on Apache-2.0 and added a top-level `LICENSE` file
- Added a release checklist so future version bumps, changelog entries, and public metadata stay in sync

### Added
- GitHub Actions smoke checks for builds, installs, imports, and console entrypoint metadata across Python 3.10-3.13
- An automated consistency checker that fails when stale `ga4_mcp_server` references or version drift appear in package-facing files

## [2.0.0] - 2025-11-09

### Changed
- Refactored the project from a single-module layout into the `ga4_mcp` package
- Added the supported launch paths `ga4-mcp-server` and `python -m ga4_mcp`
- Introduced schema discovery tools for searching dimensions and metrics by keyword or category

### Removed
- Removed the legacy `ga4_mcp_server.py` module and bundled static schema JSON files in favor of live property metadata

## [1.2.2] - 2025-09-20

### 🔧 Critical Bug Fix

#### Fixed
- **Issue #10: Multiple Filter Data Accuracy** - Fixed critical data accuracy issue where filtered queries returned incorrect user counts
  - **Root Cause**: When users applied filters, they received daily breakdowns instead of properly aggregated period totals
  - **Solution**: Improved aggregation logic to return deduplicated user counts matching GA4 browser interface
  - **Impact**: Multiple filters (3+) now return accurate data identical to GA4 browser results
  - **Verification**: Comprehensive testing with 3, 4, and 5 filter combinations across multiple date ranges

#### Added
- **Python 3.13 Support** - Added official support for Python 3.13.x
  - Tested and verified compatibility with latest Python version
  - Maintains full backward compatibility with Python 3.10-3.12

#### Technical Details
- **User Deduplication**: Fixed improper summation of daily user counts across time periods
- **Server-Side Aggregation**: Leverages GA4 API's proper aggregation when date dimension is not present
- **Filter Complexity**: Verified that filter complexity (3, 4, 5+ filters) doesn't affect accuracy
- **Browser Parity**: All filtered results now match GA4 browser interface exactly

### 📊 Verification Results
- ✅ 3 Filters (US+Desktop+Chrome): Accurate across all date ranges
- ✅ 4 Filters (+New York): Accurate across all date ranges  
- ✅ 5 Filters (+Windows): Accurate across all date ranges
- ✅ All results verified against GA4 browser interface

## [1.2.0] - 2025-01-24

### 🚀 Major Performance Enhancements

#### Added
- **Smart Data Volume Management**
  - Automatic row count estimation before data fetching
  - Interactive warning system for queries returning >2,500 rows
  - Specific optimization suggestions to reduce data volume
  - `estimate_only` parameter to get row counts without fetching data

- **Server-Side Processing Optimizations**
  - Intelligent server-side aggregation using GA4's `MetricAggregation.TOTAL`
  - Smart sorting with `OrderBy` parameters (recent dates first, highest values first)
  - Automatic aggregation detection for queries without date dimensions

- **Enhanced User Control Parameters**
  - `proceed_with_large_dataset` - Override warnings for large datasets
  - `limit` - Set maximum number of rows to return
  - `enable_aggregation` - Control server-side aggregation behavior
  - `estimate_only` - Get row estimates without data fetching

- **Response Metadata & Transparency**
  - Applied optimizations tracking in response metadata
  - Total vs returned row counts when limits are applied
  - Clear indicators when aggregation or sorting is applied

#### Changed
- Updated FastMCP dependency to `>=2.0.0` for better performance
- Enhanced `get_ga4_data` function with backward-compatible optimization parameters
- Improved error handling with graceful degradation when optimizations fail

#### Fixed
- **Context Window Crash Prevention** - Eliminates crashes from large datasets
- **Memory Usage Optimization** - Reduces memory footprint for large queries
- **API Rate Limit Management** - Better handling of GA4 API quotas

### 🔧 Technical Improvements
- Added helper functions `_get_smart_sorting()` and `_should_aggregate()`
- Enhanced response formatting with metadata for transparency
- Improved error messages and debugging information

### 📚 Documentation
- Added comprehensive optimization features section to README
- Updated usage examples with optimization scenarios
- Added troubleshooting guide for large dataset handling

## [1.0.11] - Previous Release
- Base functionality with 200+ GA4 dimensions and metrics
- Core MCP server implementation
- Basic data retrieval capabilities

---

### Migration Guide

**From v1.0.x to v1.2.0:**

No breaking changes! All existing queries will continue to work exactly as before. New optimization features are automatically enabled with sensible defaults.

**New Features You Can Use:**
```python
# Get row count estimate before fetching
get_ga4_data(dimensions=["city", "date"], estimate_only=True)

# Override warnings for large datasets
get_ga4_data(dimensions=["city", "date"], proceed_with_large_dataset=True)

# Set custom row limits
get_ga4_data(dimensions=["city"], limit=100)

# Disable automatic aggregation
get_ga4_data(dimensions=["month"], enable_aggregation=False)
```
