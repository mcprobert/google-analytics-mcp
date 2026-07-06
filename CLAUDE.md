# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Google Analytics 4 MCP server (v3.4.0) — gives AI agents analysis-ready GA4 access with zero-config OAuth login, multi-property queries, multi-account support, schema discovery, server-side aggregation, and smart defaults. Fork of surendranb/google-analytics-mcp. Built on FastMCP. Python 3.10+, Apache-2.0 license.

## Commands

### Install (development)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Install (shared network volume / multi-user, mount-independent)
When the repo lives on a shared volume that different users mount under different names (e.g. `/Volumes/Whitehat` vs `/Volumes/Whitehat1`), never bake an absolute `/Volumes/<name>` path into config or a venv. Each user runs:
```bash
bash scripts/setup_user_env.sh      # builds ~/.venvs/ga4-mcp (non-editable), copies creds to ~/.config/ga4-mcp, writes portable .mcp.json
```
The generated `.mcp.json` uses `${HOME}` expansion so one config works for everyone. Re-run the script after pulling code changes (non-editable install). On a share, also set `git config core.fileMode false` to avoid spurious mode-only diffs.

### Run
```bash
ga4-mcp-server          # console script entry point
python -m ga4_mcp       # alternative

# Optional env vars for advanced setups:
export GA4_PROPERTY_ID="123456789"          # optional default property
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"  # optional, alternative to OAuth
```

### Register an OAuth account (terminal)
```bash
ga4-mcp-add-account     # uses built-in OAuth credentials
```

### Build
```bash
python -m build
```

### Validate package consistency
```bash
python scripts/check_package_consistency.py
```
Checks version sync between `pyproject.toml` and `server.json`, ensures no legacy references remain, validates entry points and license. CI runs this on every push.

### No automated test suite
Tests are manual scenarios in `test_cases.md`. CI validates imports and package consistency across Python 3.10–3.13.

## Architecture

### Startup flow
`server.py:main()` → validates env vars → sets `metadata.DEFAULT_PROPERTY_ID` → pre-caches default property schema if set → discovers registered OAuth accounts from `~/.config/ga4-mcp/accounts/` → runs `mcp.run(transport="stdio")`.

### Coordinator pattern
`coordinator.py` declares a singleton `FastMCP` instance. All modules import `mcp` from here to register tools via `@mcp.tool()` decorators, avoiding circular imports.

### Credential management (`auth.py`)
Two credential types:
- **OAuth accounts** (primary): refresh tokens stored in `~/.config/ga4-mcp/accounts/{email}.json`. Uses embedded OAuth client credentials from `default_client.py` — no env vars needed.
- **Service account** (optional): via `GOOGLE_APPLICATION_CREDENTIALS` env var, used when no `account` param is specified.

`_get_oauth_client_config()` checks `GA4_MCP_OAUTH_CLIENT_SECRETS` env var first, then falls back to embedded credentials in `default_client.py`.

`resolve_credentials(account)` returns credentials for a specific account or `None` for service account default. All API clients accept these credentials.

Interactive OAuth uses a two-phase flow: `start_oauth_flow()` returns an auth URL and starts a local callback server; `complete_oauth_flow()` blocks until the user completes browser auth or timeout.

### Schema caching
`SCHEMA_CACHE` dict in `metadata.py` keyed by property_id. Schemas are fetched on first use per property via `_get_schema(property_id, account)` and cached for the session.

### Tool modules

**`tools/metadata.py`** — 11 tools: account management (`list_accounts`, `add_account`, `complete_account_login`, `remove_registered_account`), property discovery (`list_properties`), and schema tools (`search_schema`, `get_property_schema`, `list_dimension_categories`, `list_metric_categories`, `get_dimensions_by_category`, `get_metrics_by_category`). All accept optional `property_id` and `account` parameters.

**`tools/reporting.py`** — `get_ga4_data` tool with smart features (row estimation, auto-aggregation, intelligent sorting). Accepts `property_id` and `account` parameters.

### Response conventions
All tools return dicts. Success: `{"data": [...], "metadata": {...}}` or domain-specific keys. Errors: `{"error": "message"}`. Warnings: `{"warning": "...", "suggestions": [...]}`.

## Version & Release

Version is declared in `pyproject.toml` and must be mirrored in `server.json`. The consistency script enforces this. Upstream is `surendranb/google-analytics-mcp` (remote: `upstream`), fork is `mcprobert/google-analytics-mcp` (remote: `origin`).

## Key Constraints

- `GA4_PROPERTY_ID` is the numeric property ID, NOT the Measurement ID (G-XXXXXXX)
- Service account needs "Viewer" on GA4 property and GA4 Data API enabled
- OAuth accounts need GA4 Admin API enabled for `list_properties`
- Server uses stdio transport exclusively
- OAuth tokens stored in `~/.config/ga4-mcp/accounts/`
- Credential files (`docs/*.json`) and `.mcp.json` are gitignored — never commit secrets
