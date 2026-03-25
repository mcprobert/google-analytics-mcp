# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
