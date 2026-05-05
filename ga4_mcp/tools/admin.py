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

"""GA4 Admin API write tools — key events, data streams, custom dimensions.

Every write tool gates on ``ensure_edit_scope(account)`` before making an API call.
List tools do not require the edit scope and remain callable by read-only accounts.

Response shape for writes (standardized):
    Create:  {"status": "success", "action": "created|existed", "data": {...}}
    Delete:  {"status": "success", "action": "deleted|dry_run",  "data": {...}}
    Update:  {"status": "success", "action": "updated", "data": {...}, "updated_fields": [...]}
    Errors:  {"error": "...", ...}
"""

import json

from ga4_mcp.coordinator import mcp
from ga4_mcp.auth import resolve_credentials, ensure_edit_scope, _safe_account_path
from ga4_mcp.tools.metadata import (
    _resolve_pid_or_error,
    _property_used_meta,
    _default_used_notice,
)


class AdminClientError(Exception):
    """Raised by ``_admin_client`` when credential resolution or Admin API
    client construction fails.

    Callers catch ``AdminClientError`` and return ``.payload`` directly as
    the tool's error response — this keeps the happy path linear and makes
    it impossible to forget handling the error (unlike a tuple-return
    pattern where ``if err: return err`` can be skipped).
    """

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(payload.get("error", "admin client error"))


def _import_admin():
    """Lazily import google-analytics-admin so missing deps return a nice error."""
    try:
        from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
        from google.analytics.admin_v1beta.types import (
            KeyEvent,
            DataStream,
            CustomDimension,
            ListKeyEventsRequest,
            CreateKeyEventRequest,
            DeleteKeyEventRequest,
            ListDataStreamsRequest,
            UpdateDataStreamRequest,
            ListCustomDimensionsRequest,
            CreateCustomDimensionRequest,
        )
        from google.protobuf.field_mask_pb2 import FieldMask
        from google.protobuf.json_format import MessageToDict
        from google.api_core.exceptions import (
            AlreadyExists,
            PermissionDenied,
            NotFound,
            GoogleAPICallError,
        )
        return {
            "AnalyticsAdminServiceClient": AnalyticsAdminServiceClient,
            "KeyEvent": KeyEvent,
            "DataStream": DataStream,
            "CustomDimension": CustomDimension,
            "ListKeyEventsRequest": ListKeyEventsRequest,
            "CreateKeyEventRequest": CreateKeyEventRequest,
            "DeleteKeyEventRequest": DeleteKeyEventRequest,
            "ListDataStreamsRequest": ListDataStreamsRequest,
            "UpdateDataStreamRequest": UpdateDataStreamRequest,
            "ListCustomDimensionsRequest": ListCustomDimensionsRequest,
            "CreateCustomDimensionRequest": CreateCustomDimensionRequest,
            "FieldMask": FieldMask,
            "MessageToDict": MessageToDict,
            "AlreadyExists": AlreadyExists,
            "PermissionDenied": PermissionDenied,
            "NotFound": NotFound,
            "GoogleAPICallError": GoogleAPICallError,
        }
    except ImportError:
        return None


def _admin_client(account, adm):
    """Resolve credentials and build an Admin API client.

    Never returns None. On success, returns a constructed
    AnalyticsAdminServiceClient. On any credential resolution or client
    construction failure, raises :class:`AdminClientError` with a
    normalized error-dict payload the caller returns directly.

    Distinguishes six failure modes, each with an actionable message:
      - invalid account identifier (path traversal / malformed)
      - unknown account (token file missing)
      - corrupt token file (json.JSONDecodeError)
      - revoked refresh token (google.auth.exceptions.RefreshError)
      - network / transport error (google.auth.exceptions.TransportError)
      - missing default credentials (DefaultCredentialsError)

    Programming errors (KeyError from ``adm[...]``, AttributeError from
    proto schema bugs, etc.) propagate uncaught on purpose — those should
    surface loudly in tests rather than get masked as "re-register your
    account".
    """
    # Early account-exists check mirrors ga4_health_check's pre-check in
    # metadata.py. Using _safe_account_path + exists() instead of going
    # through resolve_credentials() sidesteps the JSONDecodeError-inherits-
    # from-ValueError trap: we can distinguish "unknown account" from
    # "corrupt token file" without the JSON parse error masquerading as a
    # ValueError in the catch block below.
    if account:
        try:
            token_file = _safe_account_path(account)
        except ValueError as e:
            raise AdminClientError({
                "error": f"Invalid account identifier '{account}': {e}",
                "account": account,
            })
        if not token_file.exists():
            raise AdminClientError({
                "error": (
                    f"No stored credentials for '{account}'. Use the add_account "
                    f"MCP tool or run `ga4-mcp-add-account` to register this account."
                ),
                "account": account,
            })

    # Lazy import so a missing google-auth (unreachable in practice — it's
    # a hard dependency) degrades gracefully rather than crashing import.
    try:
        from google.auth.exceptions import (
            RefreshError,
            TransportError,
            DefaultCredentialsError,
        )
    except ImportError:  # pragma: no cover
        RefreshError = TransportError = DefaultCredentialsError = Exception

    try:
        creds = resolve_credentials(account)
        Client = adm["AnalyticsAdminServiceClient"]
        return Client(credentials=creds) if creds else Client()
    except json.JSONDecodeError as e:
        raise AdminClientError({
            "error": (
                f"Credential file for '{account}' is corrupt or unreadable: {e}. "
                f"Re-register via the add_account MCP tool."
            ),
            "account": account,
            "exception_type": "JSONDecodeError",
        })
    except RefreshError as e:
        raise AdminClientError({
            "error": (
                f"Refresh token for '{account}' was rejected by Google: {e}. "
                f"The token may have been revoked or expired. Re-register via "
                f"the add_account MCP tool."
            ),
            "account": account,
            "exception_type": "RefreshError",
        })
    except TransportError as e:
        raise AdminClientError({
            "error": (
                f"Network error while refreshing credentials for '{account}': {e}. "
                f"Retry shortly or check network connectivity."
            ),
            "account": account,
            "exception_type": "TransportError",
        })
    except DefaultCredentialsError as e:
        raise AdminClientError({
            "error": (
                f"No default credentials available: {e}. Either pass "
                f"account=\"user@example.com\" with a registered OAuth account, "
                f"or configure GOOGLE_APPLICATION_CREDENTIALS."
            ),
            "account": account,
            "exception_type": "DefaultCredentialsError",
        })


def _to_dict(proto_message, MessageToDict):
    """Serialize a proto message to a plain dict with snake_case field names."""
    return MessageToDict(proto_message._pb, preserving_proto_field_name=True)


def _missing_admin_error():
    return {
        "error": (
            "google-analytics-admin package not installed. "
            "Run: pip install 'google-analytics-admin>=0.23.0'"
        )
    }


def _find_key_event_by_name(client, parent, event_name, adm):
    """Iterate ALL pages of list_key_events and return the matching event or None.

    KeyEvent IDs are server-generated (not derivable from event_name), so there is no
    get_key_event(event_name=...) shortcut. A full paginated list-scan is mandatory.
    """
    pager = client.list_key_events(
        request=adm["ListKeyEventsRequest"](parent=parent)
    )
    # The pager iterator yields one item per page automatically — walking it exhausts
    # all pages, not just the first.
    for ke in pager:
        if ke.event_name == event_name:
            return ke
    return None


def _find_custom_dimension(client, parent, parameter_name, scope, adm):
    """Iterate ALL pages of list_custom_dimensions for an idempotency match."""
    pager = client.list_custom_dimensions(
        request=adm["ListCustomDimensionsRequest"](parent=parent)
    )
    for cd in pager:
        if cd.parameter_name == parameter_name and cd.scope.name == scope:
            return cd
    return None


# ---------- Key events ----------


@mcp.tool()
def ga4_list_key_events(property_id: str = None, account: str = None):
    """List all key events (conversions) configured on a GA4 property.

    Read-only — does not require the analytics.edit scope.

    Args:
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
    adm = _import_admin()
    if adm is None:
        return _missing_admin_error()
    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err
    try:
        client = _admin_client(account, adm)
        parent = f"properties/{pid}"
        events = []
        for ke in client.list_key_events(request=adm["ListKeyEventsRequest"](parent=parent)):
            events.append(_to_dict(ke, adm["MessageToDict"]))
        response = {
            "status": "success",
            "property_id": pid,
            "key_events": events,
            "total": len(events),
            "property_used": _property_used_meta(pid, was_explicit, account),
        }
        notice = _default_used_notice(was_explicit, pid, account)
        if notice:
            response["notice"] = notice
        return response
    except AdminClientError as e:
        return e.payload
    except adm["PermissionDenied"] as e:
        return {"error": f"Permission denied: {e}"}
    except adm["GoogleAPICallError"] as e:
        return {"error": f"Failed to list key events: {e}"}


@mcp.tool()
def ga4_create_key_event(
    event_name: str,
    counting_method: str = "ONCE_PER_EVENT",
    property_id: str = None,
    account: str = None,
):
    """Mark an event as a key event (conversion) on a GA4 property.

    **Idempotent:** if a key event with the same event_name already exists, this
    returns the existing record with action="existed" instead of erroring.

    Args:
        event_name: The name of the event to mark as a key event (e.g., "form_submit").
        counting_method: "ONCE_PER_EVENT" (default, counts every firing) or
                         "ONCE_PER_SESSION" (counts at most once per session).
        property_id: (Optional) GA4 property ID (numeric) to write to. If omitted,
            uses GA4_PROPERTY_ID if set. Pass any property_id from list_properties()
            to write to a specific property your account can edit — you do not need a
            configured default. If that property was discovered via
            list_properties(account="..."), pass the same account here. **Writes are
            high-stakes**: prefer passing property_id explicitly to avoid silently
            modifying the wrong default property.
        account: (Optional) Registered OAuth account email used as credentials. Must
            have analytics.edit scope. Properties are credential-scoped: if a property
            was returned by list_properties(account="..."), pass the same account here.
            Use list_accounts() to see available credential accounts. Do not pass the
            literal string "default credentials".
    """
    adm = _import_admin()
    if adm is None:
        return _missing_admin_error()
    gate = ensure_edit_scope(account)
    if gate:
        return gate
    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err
    if not event_name:
        return {"error": "event_name is required."}
    if counting_method not in ("ONCE_PER_EVENT", "ONCE_PER_SESSION"):
        return {
            "error": (
                f"Invalid counting_method '{counting_method}'. "
                "Must be 'ONCE_PER_EVENT' or 'ONCE_PER_SESSION'."
            )
        }

    property_used = _property_used_meta(pid, was_explicit, account)
    default_notice = _default_used_notice(was_explicit, pid, account)

    def _annotate(response):
        response["property_used"] = property_used
        if default_notice:
            response["notice"] = default_notice
        return response

    try:
        client = _admin_client(account, adm)
        parent = f"properties/{pid}"

        # 1. Idempotency: check if this key event already exists.
        existing = _find_key_event_by_name(client, parent, event_name, adm)
        if existing is not None:
            return _annotate({
                "status": "success",
                "action": "existed",
                "data": _to_dict(existing, adm["MessageToDict"]),
            })

        # 2. Create.
        ke = adm["KeyEvent"](event_name=event_name, counting_method=counting_method)
        try:
            created = client.create_key_event(
                request=adm["CreateKeyEventRequest"](parent=parent, key_event=ke)
            )
        except adm["AlreadyExists"]:
            # Race: another caller created it between our list and create. Re-list
            # (also paginated) and return the record they created.
            raced = _find_key_event_by_name(client, parent, event_name, adm)
            if raced is not None:
                return _annotate({
                    "status": "success",
                    "action": "existed",
                    "data": _to_dict(raced, adm["MessageToDict"]),
                })
            return {
                "error": (
                    f"AlreadyExists race could not be resolved for event '{event_name}'. "
                    "Re-run ga4_list_key_events to see the current state."
                )
            }
        return _annotate({
            "status": "success",
            "action": "created",
            "data": _to_dict(created, adm["MessageToDict"]),
        })
    except AdminClientError as e:
        return e.payload
    except adm["PermissionDenied"] as e:
        return {
            "error": "Permission denied. Check that the account has analytics.edit scope and Editor access on the GA4 property.",
            "details": str(e),
        }
    except adm["GoogleAPICallError"] as e:
        return {"error": f"Failed to create key event: {e}"}


@mcp.tool()
def ga4_delete_key_event(
    event_name: str,
    dry_run: bool = False,
    property_id: str = None,
    account: str = None,
):
    """Delete a key event from a GA4 property by its event name.

    Because KeyEvent resource IDs are server-generated, this tool first lists all
    key events to resolve ``event_name`` to its full resource name, then deletes it.

    Args:
        event_name: The event_name of the key event to delete (e.g., "form_submit").
        dry_run: If True, returns what would be deleted without actually deleting it.
        property_id: (Optional) GA4 property ID (numeric) to write to. If omitted,
            uses GA4_PROPERTY_ID if set. Pass any property_id from list_properties()
            to write to a specific property your account can edit — you do not need a
            configured default. If that property was discovered via
            list_properties(account="..."), pass the same account here. **Writes are
            high-stakes**: prefer passing property_id explicitly to avoid silently
            modifying the wrong default property.
        account: (Optional) Registered OAuth account email used as credentials. Must
            have analytics.edit scope. Properties are credential-scoped: if a property
            was returned by list_properties(account="..."), pass the same account here.
            Use list_accounts() to see available credential accounts. Do not pass the
            literal string "default credentials".
    """
    adm = _import_admin()
    if adm is None:
        return _missing_admin_error()
    gate = ensure_edit_scope(account)
    if gate:
        return gate
    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err
    if not event_name:
        return {"error": "event_name is required."}

    property_used = _property_used_meta(pid, was_explicit, account)
    default_notice = _default_used_notice(was_explicit, pid, account)

    def _annotate(response):
        response["property_used"] = property_used
        if default_notice:
            response["notice"] = default_notice
        return response

    try:
        client = _admin_client(account, adm)
        parent = f"properties/{pid}"
        target = _find_key_event_by_name(client, parent, event_name, adm)
        if target is None:
            return _annotate({
                "error": f"No key event with event_name='{event_name}' found on property {pid}.",
                "property_id": pid,
                "event_name": event_name,
            })
        target_dict = _to_dict(target, adm["MessageToDict"])
        if dry_run:
            return _annotate({
                "status": "success",
                "action": "dry_run",
                "dry_run": True,
                "data": target_dict,
            })
        client.delete_key_event(
            request=adm["DeleteKeyEventRequest"](name=target.name)
        )
        return _annotate({
            "status": "success",
            "action": "deleted",
            "data": target_dict,
        })
    except AdminClientError as e:
        return e.payload
    except adm["PermissionDenied"] as e:
        return {"error": f"Permission denied: {e}"}
    except adm["NotFound"] as e:
        return {"error": f"Key event not found: {e}"}
    except adm["GoogleAPICallError"] as e:
        return {"error": f"Failed to delete key event: {e}"}


# ---------- Data streams ----------


@mcp.tool()
def ga4_list_data_streams(property_id: str = None, account: str = None):
    """List all data streams configured on a GA4 property.

    Read-only — does not require the analytics.edit scope.

    Args:
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
    adm = _import_admin()
    if adm is None:
        return _missing_admin_error()
    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err
    try:
        client = _admin_client(account, adm)
        parent = f"properties/{pid}"
        streams = []
        for ds in client.list_data_streams(
            request=adm["ListDataStreamsRequest"](parent=parent)
        ):
            streams.append(_to_dict(ds, adm["MessageToDict"]))
        response = {
            "status": "success",
            "property_id": pid,
            "data_streams": streams,
            "total": len(streams),
            "property_used": _property_used_meta(pid, was_explicit, account),
        }
        notice = _default_used_notice(was_explicit, pid, account)
        if notice:
            response["notice"] = notice
        return response
    except AdminClientError as e:
        return e.payload
    except adm["PermissionDenied"] as e:
        return {"error": f"Permission denied: {e}"}
    except adm["GoogleAPICallError"] as e:
        return {"error": f"Failed to list data streams: {e}"}


@mcp.tool()
def ga4_update_data_stream(
    stream_id: str,
    display_name: str = None,
    web_stream_data_default_uri: str = None,
    confirm: bool = False,
    property_id: str = None,
    account: str = None,
):
    """Patch a GA4 DataStream's display name and/or web default URI.

    This tool updates ONLY the display_name and (for web streams) default_uri on a
    DataStream. To change enhanced measurement settings (scrolls, outbound clicks,
    form_interactions, etc.), use the separate updateEnhancedMeasurementSettings
    sub-resource — that's out of scope for v3.2.0.

    Args:
        stream_id: Numeric data stream ID (the suffix after "dataStreams/" in the resource name).
        display_name: New display name for the stream.
        web_stream_data_default_uri: (Web streams only) new default URI (e.g., "https://example.com").
        confirm: MUST be True to execute — guards against accidental LLM-initiated changes.
        property_id: (Optional) GA4 property ID (numeric) to write to. If omitted,
            uses GA4_PROPERTY_ID if set. Pass any property_id from list_properties()
            to write to a specific property your account can edit — you do not need a
            configured default. If that property was discovered via
            list_properties(account="..."), pass the same account here. **Writes are
            high-stakes**: prefer passing property_id explicitly to avoid silently
            modifying the wrong default property.
        account: (Optional) Registered OAuth account email used as credentials. Must
            have analytics.edit scope. Properties are credential-scoped: if a property
            was returned by list_properties(account="..."), pass the same account here.
            Use list_accounts() to see available credential accounts. Do not pass the
            literal string "default credentials".
    """
    adm = _import_admin()
    if adm is None:
        return _missing_admin_error()
    gate = ensure_edit_scope(account)
    if gate:
        return gate
    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err
    if not stream_id:
        return {"error": "stream_id is required."}

    property_used = _property_used_meta(pid, was_explicit, account)
    default_notice = _default_used_notice(was_explicit, pid, account)

    def _annotate(response):
        response["property_used"] = property_used
        if default_notice:
            response["notice"] = default_notice
        return response

    if not confirm:
        # The confirm-required path is the most important place for the
        # default-property notice: an agent about to ask the user "shall I
        # confirm?" must show which property they're about to modify.
        return _annotate({
            "error": (
                "confirm=True is required to execute ga4_update_data_stream. "
                "This guards against accidental changes to visitor-facing configuration."
            ),
            "confirm_required": True,
        })
    if display_name is None and web_stream_data_default_uri is None:
        return {
            "error": (
                "No fields provided to update. Pass display_name and/or "
                "web_stream_data_default_uri."
            )
        }

    try:
        client = _admin_client(account, adm)
        name = f"properties/{pid}/dataStreams/{stream_id}"
        stream = adm["DataStream"](name=name)
        paths = []
        if display_name is not None:
            stream.display_name = display_name
            paths.append("display_name")
        if web_stream_data_default_uri is not None:
            stream.web_stream_data.default_uri = web_stream_data_default_uri
            paths.append("web_stream_data.default_uri")

        updated = client.update_data_stream(
            request=adm["UpdateDataStreamRequest"](
                data_stream=stream,
                update_mask=adm["FieldMask"](paths=paths),
            )
        )
        return _annotate({
            "status": "success",
            "action": "updated",
            "updated_fields": paths,
            "data": _to_dict(updated, adm["MessageToDict"]),
        })
    except AdminClientError as e:
        return e.payload
    except adm["PermissionDenied"] as e:
        return {"error": f"Permission denied: {e}"}
    except adm["NotFound"] as e:
        return {"error": f"Data stream not found: {e}"}
    except adm["GoogleAPICallError"] as e:
        return {"error": f"Failed to update data stream: {e}"}


# ---------- Custom dimensions ----------


@mcp.tool()
def ga4_list_custom_dimensions(property_id: str = None, account: str = None):
    """List all custom dimensions registered on a GA4 property.

    Read-only — does not require the analytics.edit scope.

    Args:
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
    adm = _import_admin()
    if adm is None:
        return _missing_admin_error()
    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err
    try:
        client = _admin_client(account, adm)
        parent = f"properties/{pid}"
        dims = []
        for cd in client.list_custom_dimensions(
            request=adm["ListCustomDimensionsRequest"](parent=parent)
        ):
            dims.append(_to_dict(cd, adm["MessageToDict"]))
        response = {
            "status": "success",
            "property_id": pid,
            "custom_dimensions": dims,
            "total": len(dims),
            "property_used": _property_used_meta(pid, was_explicit, account),
        }
        notice = _default_used_notice(was_explicit, pid, account)
        if notice:
            response["notice"] = notice
        return response
    except AdminClientError as e:
        return e.payload
    except adm["PermissionDenied"] as e:
        return {"error": f"Permission denied: {e}"}
    except adm["GoogleAPICallError"] as e:
        return {"error": f"Failed to list custom dimensions: {e}"}


@mcp.tool()
def ga4_create_custom_dimension(
    parameter_name: str,
    display_name: str,
    scope: str = "EVENT",
    description: str = None,
    allow_user_scope: bool = False,
    property_id: str = None,
    account: str = None,
):
    """Register a custom dimension on a GA4 property.

    **Idempotent** on (parameter_name, scope): if one already exists, returns it
    with action="existed" instead of erroring.

    **USER scope soft-block**: scope="USER" requires allow_user_scope=True, because
    USER-scope dimensions are stored against each user and can bloat GA4 quotas.

    Args:
        parameter_name: The event/user parameter name to map (e.g., "hubspot_contact_id").
        display_name: Human-readable name shown in the GA4 UI.
        scope: "EVENT" (default), "USER", or "ITEM".
        description: (Optional) Description shown in the GA4 UI.
        allow_user_scope: Required to be True when scope="USER". See soft-block note above.
        property_id: (Optional) GA4 property ID (numeric) to write to. If omitted,
            uses GA4_PROPERTY_ID if set. Pass any property_id from list_properties()
            to write to a specific property your account can edit — you do not need a
            configured default. If that property was discovered via
            list_properties(account="..."), pass the same account here. **Writes are
            high-stakes**: prefer passing property_id explicitly to avoid silently
            modifying the wrong default property.
        account: (Optional) Registered OAuth account email used as credentials. Must
            have analytics.edit scope. Properties are credential-scoped: if a property
            was returned by list_properties(account="..."), pass the same account here.
            Use list_accounts() to see available credential accounts. Do not pass the
            literal string "default credentials".
    """
    adm = _import_admin()
    if adm is None:
        return _missing_admin_error()
    gate = ensure_edit_scope(account)
    if gate:
        return gate
    pid, was_explicit, err = _resolve_pid_or_error(property_id)
    if err:
        return err
    if not parameter_name or not display_name:
        return {"error": "parameter_name and display_name are required."}
    scope = (scope or "EVENT").upper()
    if scope not in ("EVENT", "USER", "ITEM"):
        return {"error": f"Invalid scope '{scope}'. Must be EVENT, USER, or ITEM."}

    property_used = _property_used_meta(pid, was_explicit, account)
    default_notice = _default_used_notice(was_explicit, pid, account)

    def _annotate(response):
        response["property_used"] = property_used
        if default_notice:
            response["notice"] = default_notice
        return response

    if scope == "USER" and not allow_user_scope:
        # Soft-block early return: stamp property_used so an agent prompting
        # the user to confirm allow_user_scope=True can also confirm WHICH
        # property the dimension would land on.
        return _annotate({
            "error": (
                "USER-scope custom dimensions attach to every user record and can bloat "
                "GA4 storage quotas. Pass allow_user_scope=True to confirm you understand "
                "the cost implications."
            ),
            "parameter_name": parameter_name,
            "scope": scope,
        })

    try:
        client = _admin_client(account, adm)
        parent = f"properties/{pid}"

        # Idempotency check.
        existing = _find_custom_dimension(client, parent, parameter_name, scope, adm)
        if existing is not None:
            return _annotate({
                "status": "success",
                "action": "existed",
                "data": _to_dict(existing, adm["MessageToDict"]),
            })

        scope_enum = getattr(adm["CustomDimension"].DimensionScope, scope)
        cd = adm["CustomDimension"](
            parameter_name=parameter_name,
            display_name=display_name,
            scope=scope_enum,
        )
        if description:
            cd.description = description

        try:
            created = client.create_custom_dimension(
                request=adm["CreateCustomDimensionRequest"](
                    parent=parent, custom_dimension=cd
                )
            )
        except adm["AlreadyExists"]:
            raced = _find_custom_dimension(client, parent, parameter_name, scope, adm)
            if raced is not None:
                return _annotate({
                    "status": "success",
                    "action": "existed",
                    "data": _to_dict(raced, adm["MessageToDict"]),
                })
            return {
                "error": (
                    f"AlreadyExists race could not be resolved for custom dimension "
                    f"'{parameter_name}' (scope={scope})."
                )
            }
        return _annotate({
            "status": "success",
            "action": "created",
            "data": _to_dict(created, adm["MessageToDict"]),
        })
    except AdminClientError as e:
        return e.payload
    except adm["PermissionDenied"] as e:
        return {
            "error": "Permission denied. Check that the account has analytics.edit scope and Editor access on the GA4 property.",
            "details": str(e),
        }
    except adm["GoogleAPICallError"] as e:
        return {"error": f"Failed to create custom dimension: {e}"}
