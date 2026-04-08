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

"""Screaming Frog analytics_*.csv bridge — load SF exports locally, no API calls.

Screaming Frog's full export now includes ``analytics_*.csv`` files populated from
its GA4 / GSC integrations. Loading them here lets audits query complete snapshots
without burning GA4 Data API quota and without round-tripping through jq.

No auth, no network I/O, stdlib only.
"""

import csv
import math
import threading
import time
import uuid
from pathlib import Path

from ga4_mcp.coordinator import mcp

# Session store: {session_id: {"loaded_at": ts, "path": str, "datasets": {name: [rows]}}}
_SF_SESSIONS: dict = {}

# FastMCP runs sync tools in a thread pool; the LRU check-then-act on _SF_SESSIONS
# must be atomic. All reads AND writes acquire this lock.
_SF_LOCK = threading.Lock()

MAX_SESSIONS = 8
MAX_ROWS_PER_DATASET = 50_000
MAX_TOTAL_ROWS_PER_SESSION = 200_000
MAX_QUERY_LIMIT = 500
ALLOWED_OPS = ("eq", "contains", "gt", "gte", "lt", "lte")


def _new_session_id() -> str:
    return f"sf-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _trim_sessions_locked():
    """Drop the oldest session when over MAX_SESSIONS. Caller must hold _SF_LOCK."""
    if len(_SF_SESSIONS) <= MAX_SESSIONS:
        return
    oldest = min(_SF_SESSIONS.items(), key=lambda kv: kv[1]["loaded_at"])
    _SF_SESSIONS.pop(oldest[0], None)


def _dataset_name_from_stem(stem: str) -> str:
    """Strip leading 'analytics_' prefix so callers can say dataset='orphan_urls'."""
    lowered = stem.lower()
    if lowered.startswith("analytics_"):
        return lowered[len("analytics_"):]
    return lowered


@mcp.tool()
def ga4_load_sf_analytics_csvs(sf_export_path: str):
    """Load all ``analytics_*.csv`` files from a Screaming Frog export folder.

    Reads each matching CSV into memory, keyed by a short dataset name derived from
    the filename (e.g. ``analytics_orphan_urls.csv`` → ``orphan_urls``). Returns a
    session_id that can be passed to ``ga4_query_sf_analytics`` for filtered queries.

    Partial-load semantics: if some files fail to parse, they're reported in
    ``loaded_partial`` but the overall status is still ``success`` provided at least
    one file loaded successfully. Only an all-files-failed result returns an error.

    **Empty datasets are preserved.** A legitimately empty CSV (header row only,
    no data rows — e.g. ``analytics_orphan_urls.csv`` on a site with zero orphan
    pages) is stored as an empty queryable dataset. Subsequent
    ``ga4_query_sf_analytics(dataset="orphan_urls")`` calls return
    ``{"rows": [], "total_matched": 0, ...}`` rather than "dataset not found".
    The only zero-row case that is NOT stored is when the session row budget
    was exhausted before the first row of this file could be read — that file
    was never actually processed, and is reported in ``loaded_partial``.

    Args:
        sf_export_path: Absolute or relative path to a Screaming Frog export directory.
    """
    if not sf_export_path:
        return {"error": "sf_export_path is required."}
    root = Path(sf_export_path).expanduser()
    if not root.is_dir():
        return {"error": f"Not a directory: {root}"}

    candidates = sorted(p for p in root.glob("*.csv") if p.stem.lower().startswith("analytics_"))
    if not candidates:
        return {"error": f"No analytics_*.csv files found in {root}"}

    datasets: dict = {}
    row_counts: dict = {}
    loaded_partial: list = []
    total_rows = 0
    capped_datasets: list = []
    session_exhausted = False

    for csv_path in candidates:
        # If the session row budget was already exhausted while loading a prior
        # file, skip this one cleanly — don't even open it. Report it as
        # loaded_partial so the caller knows data was dropped.
        if session_exhausted:
            loaded_partial.append({
                "file": csv_path.name,
                "reason": "session_row_budget_exhausted_before_load",
            })
            continue

        name = _dataset_name_from_stem(csv_path.stem)
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                rows: list = []
                # Track WHY iteration stopped so we can report truncation
                # accurately — the old code lost the distinction between
                # "hit dataset cap", "hit session cap", and "file ended
                # normally", which let a half-loaded current file be reported
                # as if it were complete.
                truncated: str = ""  # "" | "dataset_cap" | "session_cap"
                for i, row in enumerate(reader):
                    if i >= MAX_ROWS_PER_DATASET:
                        truncated = "dataset_cap"
                        break
                    if total_rows + len(rows) >= MAX_TOTAL_ROWS_PER_SESSION:
                        truncated = "session_cap"
                        break
                    rows.append(row)

                # Store the dataset unless the session budget was exhausted
                # BEFORE we read any rows — in that boundary case the file
                # wasn't actually processed and we can't make claims about its
                # contents. All other zero-row cases are legitimate (header-
                # only CSV, e.g. analytics_orphan_urls.csv on a site with no
                # orphan pages) and are stored as queryable empty datasets so
                # ga4_query_sf_analytics returns {rows: [], total_matched: 0}
                # instead of "dataset not found".
                if truncated != "session_cap" or rows:
                    datasets[name] = rows
                    row_counts[name] = len(rows)
                    total_rows += len(rows)

                if truncated == "dataset_cap":
                    capped_datasets.append(name)
                elif truncated == "session_cap":
                    # The current file was truncated by the session budget.
                    # Report THIS file (not just later ones) in loaded_partial
                    # so the caller knows this dataset is incomplete.
                    loaded_partial.append({
                        "file": csv_path.name,
                        "reason": f"session_row_budget_exhausted_at_row_{len(rows)}",
                        "dataset": name if rows else None,
                        "loaded_rows": len(rows),
                    })
                    session_exhausted = True
        except (csv.Error, UnicodeDecodeError, OSError) as e:
            loaded_partial.append({"file": csv_path.name, "reason": str(e)})

    if not datasets:
        return {
            "error": "Failed to load any analytics_*.csv files.",
            "loaded_partial": loaded_partial,
            "path": str(root.resolve()),
        }

    session_id = _new_session_id()
    entry = {
        "loaded_at": time.time(),
        "path": str(root.resolve()),
        "datasets": datasets,
    }
    with _SF_LOCK:
        _SF_SESSIONS[session_id] = entry
        _trim_sessions_locked()

    return {
        "status": "success",
        "session_id": session_id,
        "path": str(root.resolve()),
        "datasets": row_counts,
        "total_rows": total_rows,
        "capped_datasets": capped_datasets,
        "loaded_partial": loaded_partial,
    }


def _coerce_number(value):
    """Best-effort numeric coercion for comparison operators.

    Returns None for blank / non-numeric input AND for NaN. NaN compares
    False against everything, which makes Python's Timsort produce
    nondeterministic order when NaN values are mixed with finite floats,
    and breaks filter-operator comparisons (gt/gte/lt/lte) in subtle
    ways. Treat NaN as non-numeric so it groups with other non-numeric
    rows at the end of sorted output.
    """
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num):
        return None
    return num


def _row_matches(row: dict, filter_dict: dict) -> bool:
    for col, condition in filter_dict.items():
        if not isinstance(condition, dict) or not condition:
            return False
        op, target = next(iter(condition.items()))
        if op not in ALLOWED_OPS:
            return False
        cell = row.get(col)
        if op == "eq":
            if str(cell) != str(target):
                return False
        elif op == "contains":
            if target is None or str(target).lower() not in str(cell or "").lower():
                return False
        else:
            left = _coerce_number(cell)
            right = _coerce_number(target)
            if left is None or right is None:
                return False
            if op == "gt" and not left > right:
                return False
            if op == "gte" and not left >= right:
                return False
            if op == "lt" and not left < right:
                return False
            if op == "lte" and not left <= right:
                return False
    return True


@mcp.tool()
def ga4_query_sf_analytics(
    session_id: str,
    dataset: str,
    filter: dict = None,
    sort_by: str = None,
    sort_desc: bool = True,
    limit: int = 100,
):
    """Query a previously loaded Screaming Frog analytics dataset.

    Filter format::

        {
            "column_name": {"eq": "value"},
            "other_column": {"contains": "substring"},
            "sessions": {"gt": 100},
        }

    Supported operators: ``eq``, ``contains``, ``gt``, ``gte``, ``lt``, ``lte``. All
    conditions are AND-ed together. Numeric comparisons coerce both sides to float;
    non-numeric cells (including NaN) are treated as non-matches.

    **Sort semantics:** ``sort_desc`` applies UNIFORMLY to numeric and text columns.
    Rows are partitioned into numeric and non-numeric groups; numeric rows sort by
    numeric value and non-numeric rows sort by string value, each group respecting
    ``sort_desc``. Non-numeric rows (blanks, NaN, unparseable strings) ALWAYS group
    at the end of the result regardless of direction — numeric values dominate the
    top-N whether you're asking for ascending or descending order.

    Args:
        session_id: Token returned by ga4_load_sf_analytics_csvs.
        dataset: Dataset name (e.g. "orphan_urls", "no_ga_data").
        filter: (Optional) Filter dict as described above.
        sort_by: (Optional) Column name to sort by.
        sort_desc: Sort descending (default True). Applies to both numeric and text columns.
        limit: Max rows to return. Capped at 500. Default 100.
    """
    if not session_id or not dataset:
        return {"error": "session_id and dataset are required."}

    with _SF_LOCK:
        session = _SF_SESSIONS.get(session_id)
        if session is None:
            return {"error": f"Unknown session_id '{session_id}'. Call ga4_load_sf_analytics_csvs first."}
        datasets = session["datasets"]
        if dataset not in datasets:
            return {
                "error": f"Dataset '{dataset}' not found in session.",
                "available_datasets": sorted(datasets.keys()),
            }
        # Copy the row list so we can release the lock before doing filter/sort work.
        rows = list(datasets[dataset])
        session_path = session["path"]

    if filter:
        if not isinstance(filter, dict):
            return {"error": "filter must be a dict of {column: {op: value}}."}
        # Validate operators up front so the caller gets a clear error.
        for col, condition in filter.items():
            if not isinstance(condition, dict) or not condition:
                return {"error": f"Filter for column '{col}' must be a non-empty dict."}
            op = next(iter(condition))
            if op not in ALLOWED_OPS:
                return {
                    "error": (
                        f"Unsupported filter operator '{op}' on column '{col}'. "
                        f"Allowed: {', '.join(ALLOWED_OPS)}."
                    )
                }
        rows = [r for r in rows if _row_matches(r, filter)]

    total_matched = len(rows)

    if sort_by:
        # Partition rows by numeric-ness so non-numeric rows always group at
        # the end of mixed columns (the intent the previous Codex round asked
        # for), BUT sort each group independently with reverse=sort_desc so
        # text columns honor the direction too. A single sort_key approach
        # cannot reverse string order via negation, which is how the previous
        # fix silently broke sort_desc for text columns. Two passes × O(n log n)
        # is negligible vs MAX_QUERY_LIMIT=500.
        numeric_rows = []
        non_numeric_rows = []
        for r in rows:
            if _coerce_number(r.get(sort_by)) is not None:
                numeric_rows.append(r)
            else:
                non_numeric_rows.append(r)
        numeric_rows.sort(
            key=lambda r: _coerce_number(r.get(sort_by)),
            reverse=sort_desc,
        )
        non_numeric_rows.sort(
            key=lambda r: str(r.get(sort_by) or ""),
            reverse=sort_desc,
        )
        rows = numeric_rows + non_numeric_rows

    capped_limit = max(1, min(int(limit or 100), MAX_QUERY_LIMIT))
    returned = rows[:capped_limit]

    return {
        "status": "success",
        "session_id": session_id,
        "dataset": dataset,
        "rows": returned,
        "total_matched": total_matched,
        "returned": len(returned),
        "limit": capped_limit,
        "path": session_path,
    }
