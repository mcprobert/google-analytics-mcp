<p align="center">
  <img src="logo.png" alt="Google Analytics MCP Logo" width="120" />


# Google Analytics MCP Server

mcp-name: io.github.surendranb/google-analytics-mcp

[![PyPI version](https://badge.fury.io/py/google-analytics-mcp.svg)](https://badge.fury.io/py/google-analytics-mcp)
[![PyPI Downloads](https://static.pepy.tech/badge/google-analytics-mcp)](https://pepy.tech/projects/google-analytics-mcp)
[![GitHub stars](https://img.shields.io/github/stars/surendranb/google-analytics-mcp?style=social)](https://github.com/surendranb/google-analytics-mcp/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/surendranb/google-analytics-mcp?style=social)](https://github.com/surendranb/google-analytics-mcp/network/members)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Made with Love](https://img.shields.io/badge/Made%20with-❤️-red.svg)](https://github.com/surendranb/google-analytics-mcp)

Connect Google Analytics 4 data to AI agents, agentic workflows, and MCP clients. Give agents analysis-ready access to website traffic, user behavior, and performance data with schema discovery, server-side aggregation, and safe defaults that reduce data wrangling.

**Built for:** AI agents, analyst copilots, and MCP runtimes across Claude, ChatGPT, Cursor, Windsurf, and custom hosts.

**v3.2 features:** GA4 Admin API write tools (mark key events, patch data streams, register custom dimensions), context-safe audit helpers (`ga4_get_landing_page_summary`, `ga4_compare_periods`, `ga4_health_check`), and a Screaming Frog CSV bridge. **Existing users must re-run `ga4-mcp-add-account` (or the `add_account` MCP tool) to grant the new `analytics.edit` scope before using any write tool** — read-only behavior is unchanged.

**v3.1 features:** Zero-config OAuth login — just install and sign in with your Google account. Multi-property queries, multi-account support, and interactive account switching from any MCP client.

</p>
---

## Why Agents Use This Server

- **Multi-property & multi-account** — query any GA4 property across multiple Google accounts in a single session
- **Interactive OAuth login** — agents can prompt users to authenticate new Google accounts on the fly
- **Analysis-ready outputs** with server-side aggregation, so agents spend more time answering questions and less time wrangling rows
- **Live schema discovery** for each GA4 property, including category-based exploration for dimensions and metrics
- **Context-safe defaults** that estimate large datasets before they blow up a conversation or workflow
- **Portable MCP surface** that works across agent runtimes, IDE copilots, and custom automation

---

## Prerequisites

**Check your Python setup:**

```bash
# Check Python version (need 3.10+)
python --version
python3 --version

# Check pip
pip --version
pip3 --version
```

**Required:**
- Python 3.10 or higher
- Google Analytics 4 property with data
- A Google account with access to the GA4 property (Viewer role or above)

---

## Step 1: Install the MCP Server

There are two supported ways to launch the server:

- `ga4-mcp-server` when the installed console script is available on your `PATH`
- `python -m ga4_mcp` when you want to use a specific interpreter or virtual environment

### Method A: Install from PyPI (Recommended)

```bash
python3 -m pip install google-analytics-mcp
```

If your machine uses `python` instead of `python3`, run:

```bash
python -m pip install google-analytics-mcp
```

#### Option 1: Use the console script

Use this when `ga4-mcp-server` is available on your `PATH`:

```json
{
  "mcpServers": {
    "ga4-analytics": {
      "command": "ga4-mcp-server"
    }
  }
}
```

#### Option 2: Use an explicit Python interpreter

Use this when you want to pin the exact Python runtime or when the console script is not on your `PATH`:

```json
{
  "mcpServers": {
    "ga4-analytics": {
      "command": "python3",
      "args": ["-m", "ga4_mcp"]
    }
  }
}
```

**That's it!** On first use, the agent will prompt you to sign in with your Google account via the `add_account` tool. No service account or environment variables needed.

**Optional environment variables** (for advanced setups):

| Variable | Description |
|---|---|
| `GA4_PROPERTY_ID` | Default numeric GA4 property ID (avoids passing it on every tool call) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a service account JSON key (alternative to OAuth login) |

### Method B: Install from a local clone

```bash
git clone https://github.com/surendranb/google-analytics-mcp.git
cd google-analytics-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

If you plan to modify the package locally, use `python -m pip install -e .` instead.

**MCP Configuration:**

```json
{
  "mcpServers": {
    "ga4-analytics": {
      "command": "/full/path/to/google-analytics-mcp/.venv/bin/python",
      "args": ["-m", "ga4_mcp"]
    }
  }
}
```

---

## Step 2: Sign In With Your Google Account

On first use, sign in so the server can access your GA4 data. You can do this two ways:

**From your MCP client (recommended):** Ask the agent to add an account — it will call the `add_account` tool and present a Google sign-in URL. Click it, sign in, and you're done.

**From the terminal:**

```bash
ga4-mcp-add-account
```

This opens your browser for Google login. Once authenticated, the account is stored locally and available on every future server start.

You can register multiple Google accounts and switch between them using the `account` parameter on any tool.

---

## Multi-Property & Multi-Account Usage

### Querying Different Properties

All tools accept an optional `property_id` parameter. If omitted, the default `GA4_PROPERTY_ID` is used:

```
Show me sessions for property 394323647 for the last 7 days
```

### Switching Accounts

Register additional Google accounts with `add_account`, then use the `account` parameter:

```
Show me traffic for property 123456789 using account alice@company.com
```

Use `list_accounts` to see all registered accounts and `list_properties` to discover which GA4 properties each account can access.

---

## Usage

Once configured, ask your MCP client questions like:

### Discovery & Exploration
- What GA4 dimension categories are available?
- Show me all ecommerce metrics
- What dimensions can I use for geographic analysis?

### Traffic Analysis
- What's my website traffic for the past week?
- Show me user metrics by city for last month
- Compare bounce rates between different date ranges

### Multi-Dimensional Analysis
- Show me revenue by country and device category for last 30 days
- Analyze sessions and conversions by campaign and source/medium
- Compare user engagement across different page paths and traffic sources

### E-commerce Analysis
- What are my top-performing products by revenue?
- Show me conversion rates by traffic source and device type
- Analyze purchase behavior by user demographics

---

## Quick Start Examples

Try these example queries to see the MCP's analytical capabilities:

### 1. Geographic Distribution
```
Show me a map of visitors by city for the last 30 days, with a breakdown of new vs returning users
```
This demonstrates:
- Geographic analysis
- User segmentation
- Time-based filtering
- Data visualization

### 2. User Behavior Analysis
```
Compare average session duration and pages per session by device category and browser over the last 90 days
```
This demonstrates:
- Multi-dimensional analysis
- Time series comparison
- User engagement metrics
- Technology segmentation

### 3. Traffic Source Performance
```
Show me conversion rates and revenue by traffic source and campaign, comparing last 30 days vs previous 30 days
```
This demonstrates:
- Marketing performance analysis
- Period-over-period comparison
- Conversion tracking
- Revenue attribution

### 4. Content Performance
```
What are my top 10 pages by engagement rate, and how has their performance changed over the last 3 months?
```
This demonstrates:
- Content analysis
- Trend analysis
- Engagement metrics
- Ranking and sorting

---

## 🚀 Performance Optimizations

This MCP server includes **built-in optimizations** to prevent context window crashes and ensure smooth operation:

### Smart Data Volume Management
- **Automatic row estimation** - Checks data volume before fetching
- **Interactive warnings** - Alerts when queries would return >2,500 rows
- **Optimization suggestions** - Provides specific recommendations to reduce data volume

### Server-Side Processing
- **Intelligent aggregation** - Automatically aggregates data when beneficial (e.g., totals across time periods)
- **Smart sorting** - Returns most relevant data first (recent dates, highest values)
- **Efficient filtering** - Leverages GA4's server-side filtering capabilities

### User Control Parameters
- `limit` - Set maximum number of rows to return
- `proceed_with_large_dataset=True` - Override warnings for large datasets
- `enable_aggregation=False` - Disable automatic aggregation
- `estimate_only=True` - Get row count estimates without fetching data

### Example: Handling Large Datasets
```python
# This query would normally return 2,605 rows and crash context window
get_ga4_data(
    dimensions=["date", "pagePath", "country"],
    date_range_start="90daysAgo"
)
# Returns: {"warning": True, "estimated_rows": 2605, "suggestions": [...]}

# Use monthly aggregation instead
get_ga4_data(
    dimensions=["month", "pagePath", "country"], 
    date_range_start="90daysAgo"
)
# Returns: Clean monthly data with manageable row count
```

---

## Available Tools

All data tools accept optional `property_id` and `account` parameters. Tools marked *(write)* require an OAuth account with the `analytics.edit` scope — re-run `ga4-mcp-add-account` to upgrade.

### Account & Property Management
1.  **`list_accounts`** - Show available service account and registered OAuth accounts, including `granted_scopes` and `can_edit`
2.  **`add_account`** - Start interactive OAuth login (returns URL for user to click)
3.  **`complete_account_login`** - Complete OAuth login after user authenticates in browser
4.  **`remove_registered_account`** - Remove a registered OAuth account
5.  **`list_properties`** - List GA4 properties accessible by a given account (supports `name_contains` filter)

### Schema Discovery
6.  **`search_schema`** - Search for dimensions and metrics by keyword (most efficient discovery method)
7.  **`get_property_schema`** - Returns the complete schema for a property (Warning: large object)
8.  **`list_dimension_categories`** - Lists all available dimension categories
9.  **`list_metric_categories`** - Lists all available metric categories
10. **`get_dimensions_by_category`** - Gets all dimensions for a specific category
11. **`get_metrics_by_category`** - Gets all metrics for a specific category

### Data Reporting
12. **`get_ga4_data`** - Retrieve GA4 data with smart features (data volume protection, server-side aggregation, intelligent sorting)
13. **`ga4_get_landing_page_summary`** *(v3.2)* - Context-safe landing-page audit with server-side totals and rot/engagement diagnostics
14. **`ga4_compare_periods`** *(v3.2)* - Two-period delta comparison with outer-join on dimension tuples

### Health & Diagnostics *(v3.2)*
15. **`ga4_health_check`** - One-shot audit diagnostic (data streams, recent events, key-event presence, scope visibility)

### GA4 Admin (write) *(v3.2)*
16. **`ga4_list_key_events`** - List all key events (conversions) on a property
17. **`ga4_create_key_event`** *(write)* - Mark an event as a key event (idempotent)
18. **`ga4_delete_key_event`** *(write)* - Delete a key event by event_name (supports `dry_run`)
19. **`ga4_list_data_streams`** - List data streams on a property
20. **`ga4_update_data_stream`** *(write)* - Patch a data stream's display name or default URI (requires `confirm=True`)
21. **`ga4_list_custom_dimensions`** - List custom dimensions on a property
22. **`ga4_create_custom_dimension`** *(write)* - Register a custom dimension (idempotent; USER scope soft-blocked behind `allow_user_scope=True`)

### Screaming Frog CSV Bridge *(v3.2)*
23. **`ga4_load_sf_analytics_csvs`** - Load `analytics_*.csv` files from an SF export folder into an in-memory session
24. **`ga4_query_sf_analytics`** - Query a loaded dataset with filter/sort/limit (no API quota burned)

---

## Dimensions & Metrics

Access to **200+ GA4 dimensions and metrics** organized by category:

### Dimension Categories
- **Time**: date, hour, month, year, etc.
- **Geography**: country, city, region
- **Technology**: browser, device, operating system
- **Traffic Source**: campaign, source, medium, channel groups
- **Content**: page paths, titles, content groups
- **E-commerce**: item details, transaction info
- **User Demographics**: age, gender, language
- **Google Ads**: campaign, ad group, keyword data
- And 10+ more categories

### Metric Categories
- **User Metrics**: totalUsers, newUsers, activeUsers
- **Session Metrics**: sessions, bounceRate, engagementRate
- **E-commerce**: totalRevenue, transactions, conversions
- **Events**: eventCount, conversions, event values
- **Advertising**: adRevenue, returnOnAdSpend
- And more specialized metrics

---

## Troubleshooting

**If `ga4-mcp-server` is not found:**
- Use the explicit interpreter launch style instead: `python -m ga4_mcp`
- Reinstall with the same Python interpreter your MCP client will use

**If you get `No module named ga4_mcp`:**
```bash
/full/path/to/python -m pip install google-analytics-mcp
```
Install the package with the exact interpreter you reference in your MCP configuration.

**Permission errors:**
```bash
# Try user install instead of system-wide
python -m pip install --user google-analytics-mcp
```

**If the server says the credentials file is missing:**
1. **Verify the JSON file path** is absolute, correct, and accessible
2. **Check service account permissions**:
   - Go to Google Cloud Console → IAM & Admin → IAM
   - Find your service account → Check permissions
3. **Verify GA4 access**:
   - GA4 → Admin → Property access management
   - Check for your service account email

**If the server says `GA4_PROPERTY_ID` is invalid or queries return no data:**
- Use the numeric **Property ID** (for example `123456789`)
- Do **not** use the **Measurement ID** (for example `G-XXXXXXXXXX`)
- Confirm the service account has at least Viewer access on that property

**API quota/rate limit errors:**
- GA4 has daily quotas and rate limits
- Try reducing the date range in your queries
- Wait a few minutes between large requests

---

## Project Structure

```
google-analytics-mcp/
├── ga4_mcp/                # Main package directory
│   ├── server.py           # Core server logic
│   ├── coordinator.py      # MCP singleton instance
│   ├── auth.py             # Multi-account credential management + scope gating
│   ├── default_client.py   # Embedded OAuth client credentials
│   ├── add_account.py      # CLI for interactive OAuth registration
│   └── tools/              # Tool definitions
│       ├── metadata.py     # Schema discovery, account management, health check
│       ├── reporting.py    # Data retrieval + landing page summary + compare periods
│       ├── admin.py        # GA4 Admin API write tools (key events, data streams, custom dimensions)
│       └── sf_bridge.py    # Screaming Frog analytics_*.csv loader + local query interface
├── scripts/                # Utility scripts
├── pyproject.toml          # Package configuration
├── README.md               # This file
└── ...
```

---

## License

Apache License 2.0
