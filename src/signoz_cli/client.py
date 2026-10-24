"""HTTP client for the SigNoz API (targets SigNoz 0.136+ / 0.138+)."""

from __future__ import annotations

import email.utils
import time
from typing import Any

import httpx

from signoz_cli.query import build_promql_query_payload, build_sql_query_payload

_RETRYABLE = {429, 500, 502, 503, 504}


def _parse_retry_after(value: str) -> int | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds.

    Returns None when the header is absent or unparseable.
    """
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    delta = parsed.timestamp() - time.time()
    return max(0, int(delta))


class SigNozApiError(Exception):
    """Raised when the SigNoz API returns an error response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


class SigNozClient:
    """Thin wrapper around the SigNoz HTTP API."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        timeout: float = 120.0,
        verify: bool = True,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.max_retries = max_retries
        self._client = httpx.Client(
            timeout=timeout,
            verify=verify,
            headers={
                "SIGNOZ-API-KEY": token,
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SigNozClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def verify(self) -> Any:
        """Validate the API key against /api/v1/service_accounts/me."""
        return self._get("/api/v1/service_accounts/me")

    def get_server_version(self) -> Any:
        """Fetch the SigNoz server version ({\"version\": \"vX.Y.Z\", ...})."""
        return self._get("/api/v1/version")

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def list_metric_keys(self) -> Any:
        # /api/v1/metrics/filters/keys was removed by 0.138; use v3 autocomplete
        return self._get(
            "/api/v3/autocomplete/aggregate_attributes",
            params={
                "dataSource": "metrics",
                "aggregateOperator": "avg",
                "searchText": "",
            },
        )

    def search_metric_by_text(self, search_text: str) -> Any:
        return self._get(
            "/api/v3/autocomplete/aggregate_attributes",
            params={"dataSource": "metrics", "searchText": search_text},
        )

    def get_metrics_available_fields(self, search_text: str = "") -> Any:
        return self._get(
            "/api/v3/autocomplete/aggregate_attributes",
            params={
                "aggregateOperator": "avg",
                "searchText": search_text,
                "dataSource": "metrics",
            },
        )

    def get_metrics_field_values(self, field_name: str, search_text: str = "") -> Any:
        return self._get(
            "/api/v1/fields/keys",
            params={
                "signal": "metrics",
                "metricName": field_name,
                "searchText": search_text,
                "fieldContext": "",
                "fieldDataType": "",
                "source": "",
            },
        )

    # ------------------------------------------------------------------
    # Alerts (rules) + history v2
    # ------------------------------------------------------------------

    def list_alerts(self) -> Any:
        return self._get("/api/v1/alerts")

    def list_rules(self) -> Any:
        """List all alert rule definitions (for export/migration)."""
        return self._get("/api/v1/rules")

    def list_rules_v3(
        self,
        query: str | None = None,
        states: list[str] | None = None,
        sort: str | None = None,
        order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        """GET /api/v3/rules (SigNoz 0.144+: filter DSL, states, sort, paging).

        ``states`` is sent as repeated query params. Returns rules plus
        the org's label pairs and reserved filter keywords.
        """
        from urllib.parse import urlencode

        params: list[tuple[str, str]] = []
        if query:
            params.append(("query", query))
        for state in states or []:
            params.append(("states", state))
        if sort:
            params.append(("sort", sort))
        if order:
            params.append(("order", order))
        if limit is not None:
            params.append(("limit", str(limit)))
        if offset is not None:
            params.append(("offset", str(offset)))
        qs = ("?" + urlencode(params)) if params else ""
        return self._parse(self._request("GET", f"/api/v3/rules{qs}"))

    def get_alert(self, rule_id: str) -> Any:
        return self._get(f"/api/v1/rules/{rule_id}")

    def get_alert_history_timeline(
        self,
        rule_id: str,
        start: int,
        end: int,
        limit: int | None = None,
        order: str | None = None,
        state: str | None = None,
        filter_expression: str | None = None,
        cursor: str | None = None,
    ) -> Any:
        """GET /api/v2/rules/{id}/history/timeline (cursor-paginated)."""
        params: dict[str, str] = {"start": str(start), "end": str(end)}
        if limit is not None:
            params["limit"] = str(limit)
        if order:
            params["order"] = order
        if state:
            params["state"] = state
        if filter_expression:
            params["filterExpression"] = filter_expression
        if cursor:
            params["cursor"] = cursor
        return self._get(f"/api/v2/rules/{rule_id}/history/timeline", params=params)

    def get_alert_history_stats(self, rule_id: str, start: int, end: int) -> Any:
        return self._get(
            f"/api/v2/rules/{rule_id}/history/stats",
            params={"start": str(start), "end": str(end)},
        )

    def get_alert_history_overall_status(self, rule_id: str, start: int, end: int) -> Any:
        return self._get(
            f"/api/v2/rules/{rule_id}/history/overall_status",
            params={"start": str(start), "end": str(end)},
        )

    def get_alert_history_top_contributors(self, rule_id: str, start: int, end: int) -> Any:
        return self._get(
            f"/api/v2/rules/{rule_id}/history/top_contributors",
            params={"start": str(start), "end": str(end)},
        )

    def get_history_filter_keys(
        self,
        rule_id: str,
        signal: str = "traces",
        limit: int = 100,
        start: int | None = None,
        end: int | None = None,
    ) -> Any:
        """GET /api/v2/rules/{id}/history/filter_keys (field discovery)."""
        params: dict[str, str] = {"signal": signal, "limit": str(limit)}
        if start is not None:
            params["startUnixMilli"] = str(start)
        if end is not None:
            params["endUnixMilli"] = str(end)
        return self._get(
            f"/api/v2/rules/{rule_id}/history/filter_keys", params=params)

    def get_history_filter_values(
        self,
        rule_id: str,
        field_name: str,
        signal: str = "traces",
        limit: int = 100,
        search_text: str = "",
        start: int | None = None,
        end: int | None = None,
    ) -> Any:
        """GET /api/v2/rules/{id}/history/filter_values (value discovery)."""
        params: dict[str, str] = {
            "signal": signal,
            "name": field_name,
            "limit": str(limit),
        }
        if search_text:
            params["searchText"] = search_text
        if start is not None:
            params["startUnixMilli"] = str(start)
        if end is not None:
            params["endUnixMilli"] = str(end)
        return self._get(
            f"/api/v2/rules/{rule_id}/history/filter_values", params=params)

    # ------------------------------------------------------------------
    # Dashboards v2 (Perses-compatible schema, required on 0.135+)
    # ------------------------------------------------------------------

    def list_dashboards_v2(
        self,
        limit: int | None = None,
        offset: int | None = None,
        query: str | None = None,
        sort: str | None = None,
        order: str | None = None,
    ) -> Any:
        params: dict[str, str] = {}
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)
        if query:
            params["query"] = query
        if sort:
            params["sort"] = sort
        if order:
            params["order"] = order
        return self._get("/api/v2/dashboards", params=params)

    def get_dashboard_v2(self, dashboard_id: str) -> Any:
        return self._get(f"/api/v2/dashboards/{dashboard_id}")

    def create_dashboard_v2(self, dashboard: dict[str, Any]) -> Any:
        return self._post("/api/v2/dashboards", json_body=dashboard, idempotent=False)

    def update_dashboard_v2(self, dashboard_id: str, dashboard: dict[str, Any]) -> Any:
        return self._put(f"/api/v2/dashboards/{dashboard_id}", json_body=dashboard)

    def patch_dashboard_v2(self, dashboard_id: str, patches: list[dict[str, Any]]) -> Any:
        return self._patch(f"/api/v2/dashboards/{dashboard_id}", json_body=patches, idempotent=False)

    def delete_dashboard_v2(self, dashboard_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v2/dashboards/{dashboard_id}")
        return self._parse(resp, allow_empty=True)

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

    def list_services(self, start: str, end: str) -> Any:
        return self._post("/api/v1/services", json_body={"start": start, "end": end})

    def get_service_top_operations(
        self,
        service: str,
        start: str,
        end: str,
        tags: Any | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "start": start,
            "end": end,
            "service": service,
        }
        if tags is not None:
            payload["tags"] = tags
        return self._post("/api/v1/service/top_operations", json_body=payload)

    # ------------------------------------------------------------------
    # Field discovery (traces / logs / metrics)
    # ------------------------------------------------------------------

    def get_trace_field_values(self, field_name: str, search_text: str = "") -> Any:
        return self._get(
            "/api/v1/fields/values",
            params={
                "signal": "traces",
                "name": field_name,
                "searchText": search_text,
                "metricName": "",
                "source": "meter",
            },
        )

    def get_trace_available_fields(self, search_text: str = "") -> Any:
        return self._get(
            "/api/v3/autocomplete/attribute_keys",
            params={
                "aggregateOperator": "noop",
                "searchText": search_text,
                "dataSource": "traces",
                "aggregateAttribute": "",
                "tagType": "",
            },
        )

    def get_logs_available_fields(self, search_text: str = "") -> Any:
        return self._get(
            "/api/v3/filter_suggestions",
            params={
                "searchText": search_text,
                "dataSource": "logs",
                "existingFilter": "e30",
            },
        )

    def get_logs_field_values(self, field_name: str, search_text: str = "") -> Any:
        return self._get(
            "/api/v3/autocomplete/attribute_values",
            params={
                "aggregateOperator": "noop",
                "dataSource": "logs",
                "aggregateAttribute": "",
                "attributeKey": field_name,
                "searchText": search_text,
                "filterAttributeKeyDataType": "string",
                "tagType": "resource",
            },
        )

    # ------------------------------------------------------------------
    # Query Builder v5
    # ------------------------------------------------------------------

    def query_builder_v5(self, payload: dict[str, Any]) -> Any:
        return self._post("/api/v5/query_range", json_body=payload)

    def query_promql(self, query: str, start: int, end: int, step: int = 60) -> Any:
        # Reuse the shared builder so payload shape stays in one place and the
        # step interval is actually forwarded (the old inline payload dropped it).
        payload = build_promql_query_payload(start, end, query, step_interval=step)
        return self._post("/api/v5/query_range", json_body=payload)

    def query_sql(self, query: str, start: int, end: int) -> Any:
        payload = build_sql_query_payload(start, end, query)
        return self._post("/api/v5/query_range", json_body=payload)

    def get_trace_detail(self, trace_id: str, start: int, end: int) -> Any:
        """POST /api/v4/traces/{id}/waterfall — full spans incl. attributes."""
        resp = self._client.post(
            f"{self.endpoint}/api/v4/traces/{trace_id}/waterfall"
            f"?start={start}&end={end}",
            json={},
        )
        return self._parse(resp)

    def get_flamegraph(self, trace_id: str, selected_span_id: str = "") -> Any:
        """POST /api/v3/traces/{id}/flamegraph (SigNoz 0.144+).

        The route only accepts POST (a GET falls through to the SPA page).
        """
        return self._post(f"/api/v3/traces/{trace_id}/flamegraph",
                          json_body={"selectedSpanId": selected_span_id})

    # ------------------------------------------------------------------
    # Alert rule management (v2)
    # ------------------------------------------------------------------

    def create_rule(self, rule: dict[str, Any]) -> Any:
        return self._post("/api/v2/rules", json_body=rule, idempotent=False)

    def update_rule(self, rule_id: str, rule: dict[str, Any]) -> Any:
        return self._put(f"/api/v2/rules/{rule_id}", json_body=rule)

    def patch_rule(self, rule_id: str, patches: list[dict[str, Any]]) -> Any:
        return self._patch(f"/api/v2/rules/{rule_id}", json_body=patches, idempotent=False)

    def delete_rule(self, rule_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v2/rules/{rule_id}")
        return self._parse(resp, allow_empty=True)

    def test_rule(self, rule: dict[str, Any]) -> Any:
        return self._post("/api/v2/rules/test", json_body=rule)

    # ------------------------------------------------------------------
    # Planned maintenance (downtime schedules: mute alerts in a window)
    # ------------------------------------------------------------------

    def list_downtime_schedules(self) -> Any:
        return self._get("/api/v1/downtime_schedules")

    def get_downtime_schedule(self, schedule_id: str) -> Any:
        return self._get(f"/api/v1/downtime_schedules/{schedule_id}")

    def create_downtime_schedule(self, schedule: dict[str, Any]) -> Any:
        return self._post("/api/v1/downtime_schedules", json_body=schedule,
                          idempotent=False)

    def update_downtime_schedule(
        self, schedule_id: str, schedule: dict[str, Any]
    ) -> Any:
        resp = self._request(
            "PUT", f"/api/v1/downtime_schedules/{schedule_id}", json_body=schedule
        )
        return self._parse(resp, allow_empty=True)

    def delete_downtime_schedule(self, schedule_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v1/downtime_schedules/{schedule_id}")
        return self._parse(resp, allow_empty=True)

    # ------------------------------------------------------------------
    # Notification channels (admin)
    # ------------------------------------------------------------------

    def list_channels(self) -> Any:
        return self._get("/api/v1/channels")

    def get_channel(self, channel_id: str) -> Any:
        return self._get(f"/api/v1/channels/{channel_id}")

    def create_channel(self, channel: dict[str, Any]) -> Any:
        return self._post("/api/v1/channels", json_body=channel, idempotent=False)

    def test_channel(self, name: str) -> Any:
        """Fire a test notification through the named channel (by name)."""
        return self._post("/api/v1/channels/test", json_body={"name": name},
                          idempotent=False)

    def update_channel(self, channel_id: str, channel: dict[str, Any]) -> Any:
        return self._put(f"/api/v1/channels/{channel_id}", json_body=channel)

    def delete_channel(self, channel_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v1/channels/{channel_id}")
        return self._parse(resp, allow_empty=True)

    # ------------------------------------------------------------------
    # Notification channels v2 (SigNoz 0.141+: paginated list, repair)
    # ------------------------------------------------------------------

    def list_channels_v2(
        self,
        limit: int | None = None,
        offset: int | None = None,
        query: str | None = None,
        kind: str | None = None,
        sort: str | None = None,
        order: str | None = None,
    ) -> Any:
        """GET /api/v2/notification_channels (paginated, searchable).

        Entries carry identity and kind but not configuration; fetch by
        ID for the full document.
        """
        params: dict[str, str] = {}
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)
        if query:
            params["query"] = query
        if kind:
            params["kind"] = kind
        if sort:
            params["sort"] = sort
        if order:
            params["order"] = order
        return self._get("/api/v2/notification_channels", params=params)

    def get_channel_v2(self, channel_id: str) -> Any:
        return self._get(f"/api/v2/notification_channels/{channel_id}")

    def create_channel_v2(self, channel: dict[str, Any]) -> Any:
        return self._post("/api/v2/notification_channels",
                          json_body=channel, idempotent=False)

    def update_channel_v2(self, channel_id: str, channel: dict[str, Any]) -> Any:
        return self._put(f"/api/v2/notification_channels/{channel_id}",
                         json_body=channel)

    def delete_channel_v2(self, channel_id: str) -> Any:
        resp = self._request(
            "DELETE", f"/api/v2/notification_channels/{channel_id}")
        return self._parse(resp, allow_empty=True)

    def repair_channel_v2(self, channel_id: str, apply: bool = False) -> Any:
        """Diagnose (and optionally fix) a channel the v2 API cannot read.

        Defaults to dry-run: the server only reports what it would do
        unless ``apply`` is true.
        """
        return self._parse(
            self._request(
                "POST",
                f"/api/v2/notification_channels/{channel_id}/repair",
                params={"apply": "true" if apply else "false"},
                json_body={"apply": apply},
                idempotent=False,
            )
        )

    def test_channel_v2(self, channel: dict[str, Any]) -> Any:
        """Test a full v2 channel document (returns 204 on success)."""
        resp = self._request(
            "POST", "/api/v2/notification_channels/test", json_body=channel)
        return self._parse(resp, allow_empty=True)

    # ------------------------------------------------------------------
    # AI observability (SigNoz 0.140+: LLM spans, model pricing)
    # ------------------------------------------------------------------

    def get_ai_fields_keys(self, search_text: str = "") -> Any:
        return self._get(
            "/api/v1/ai_observability/fields/keys",
            params={"searchText": search_text} if search_text else None,
        )

    def get_ai_field_values(self, field_name: str, search_text: str = "") -> Any:
        params: dict[str, str] = {"name": field_name}
        if search_text:
            params["searchText"] = search_text
        return self._get("/api/v1/ai_observability/fields/values", params=params)

    def list_llm_pricing_rules(self) -> Any:
        return self._get("/api/v1/llm_pricing_rules")

    def create_llm_pricing_rule(self, rule: Any) -> Any:
        """PUT /api/v1/llm_pricing_rules — bulk upsert, never replaces others.

        Accepts one rule, a list of rules, or a ``{"rules": [...]}`` body.
        The server answers 204 with no body.
        """
        if isinstance(rule, dict) and isinstance(rule.get("rules"), list):
            body = rule
        elif isinstance(rule, list):
            body = {"rules": rule}
        else:
            body = {"rules": [rule]}
        resp = self._request("PUT", "/api/v1/llm_pricing_rules",
                             json_body=body, idempotent=False)
        return self._parse(resp, allow_empty=True)

    def get_llm_pricing_rule(self, rule_id: str) -> Any:
        return self._get(f"/api/v1/llm_pricing_rules/{rule_id}")

    def delete_llm_pricing_rule(self, rule_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v1/llm_pricing_rules/{rule_id}")
        return self._parse(resp, allow_empty=True)

    def list_unmapped_llm_models(self) -> Any:
        return self._get("/api/v1/llm_pricing_rules/unmapped_models")

    # ------------------------------------------------------------------
    # Saved views (0.137+ spec-based API)
    # ------------------------------------------------------------------

    def list_saved_views(self, source_page: str = "logs") -> Any:
        # NOTE: /api/v1/saved_views is gone on 0.138+ (SPA fallback page);
        # the explorer views family is the working path on both versions.
        return self._get("/api/v1/explorer/views", params={"sourcePage": source_page})

    def get_saved_view(self, view_id: str) -> Any:
        return self._get(f"/api/v1/explorer/views/{view_id}")

    def create_saved_view(self, view: dict[str, Any]) -> Any:
        return self._post("/api/v1/explorer/views", json_body=view, idempotent=False)

    def delete_saved_view(self, view_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v1/explorer/views/{view_id}")
        return self._parse(resp, allow_empty=True)

    # ------------------------------------------------------------------
    # Service accounts (admin)
    # ------------------------------------------------------------------

    def list_service_accounts(self) -> Any:
        return self._get("/api/v1/service_accounts")

    def create_service_account(
        self, name: str, role_ids: list[str] | None = None
    ) -> tuple[Any, list[str]]:
        """Create a service account and optionally assign roles.

        Returns (response, failed_role_ids) so callers can warn when a role
        could not be assigned instead of failing silently.
        """
        data = self._post("/api/v1/service_accounts", json_body={"name": name}, idempotent=False)
        failed_roles: list[str] = []
        if role_ids and isinstance(data, dict):
            new_id = (data.get("data") or {}).get("id")
            if new_id:
                for rid in role_ids:
                    try:
                        self._post(
                            "/api/v1/service_account_roles",
                            json_body={"serviceAccountId": new_id, "roleId": rid},
                            idempotent=False,
                        )
                    except SigNozApiError as exc:
                        failed_roles.append(f"{rid}: {exc.message}")
        return data, failed_roles

    def delete_service_account(self, account_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v1/service_accounts/{account_id}")
        return self._parse(resp, allow_empty=True)

    def list_roles(self) -> Any:
        return self._get("/api/v1/roles")

    def list_service_account_keys(self, account_id: str) -> Any:
        return self._get(f"/api/v1/service_accounts/{account_id}/keys")

    def create_service_account_key(
        self, account_id: str, name: str, expires_at: int = 0
    ) -> Any:
        """POST /api/v1/service_accounts/{id}/keys; expiresAt is unix seconds, 0 = never.

        The response is the only time the key value is returned.
        """
        return self._post(f"/api/v1/service_accounts/{account_id}/keys",
                          json_body={"name": name, "expiresAt": expires_at},
                          idempotent=False)

    def update_service_account_key(
        self, account_id: str, key_id: str, name: str, expires_at: int = 0
    ) -> Any:
        return self._send("PUT", f"/api/v1/service_accounts/{account_id}/keys/{key_id}",
                          json_body={"name": name, "expiresAt": expires_at})

    def revoke_service_account_key(self, account_id: str, key_id: str) -> Any:
        return self._send("DELETE", f"/api/v1/service_accounts/{account_id}/keys/{key_id}")

    # ------------------------------------------------------------------
    # Users and roles (admin, v2 users API)
    # ------------------------------------------------------------------

    def list_users(self) -> Any:
        return self._get("/api/v2/users")

    def get_user(self, user_id: str) -> Any:
        """GET /api/v2/users/{id} — includes userRoles (with their ids)."""
        return self._get(f"/api/v2/users/{user_id}")

    def get_me(self) -> Any:
        return self._get("/api/v2/users/me")

    def invite_user(
        self, email: str, display_name: str = "", role_ids: list[str] | None = None,
        frontend_base_url: str = "",
    ) -> Any:
        body: dict[str, Any] = {"email": email}
        if display_name:
            body["displayName"] = display_name
        if role_ids:
            body["userRoles"] = [{"id": rid} for rid in role_ids]
        if frontend_base_url:
            body["frontendBaseUrl"] = frontend_base_url
        return self._post("/api/v2/users", json_body=body, idempotent=False)

    def update_user(self, user_id: str, display_name: str) -> Any:
        return self._send("PUT", f"/api/v2/users/{user_id}",
                          json_body={"displayName": display_name})

    def delete_user(self, user_id: str) -> Any:
        return self._send("DELETE", f"/api/v2/users/{user_id}")

    def get_user_roles(self, user_id: str) -> Any:
        return self._get(f"/api/v2/users/{user_id}/roles")

    def assign_user_role(self, user_id: str, role_id: str) -> Any:
        return self._post("/api/v2/user_roles",
                          json_body={"userId": user_id, "roleId": role_id},
                          idempotent=False)

    def delete_user_role(self, user_role_id: str) -> Any:
        """DELETE /api/v2/user_roles/{id} — the id of the assignment, not the role."""
        return self._send("DELETE", f"/api/v2/user_roles/{user_role_id}")

    def get_reset_password_token(self, user_id: str) -> Any:
        return self._get(f"/api/v2/users/{user_id}/reset_password_tokens")

    def create_reset_password_token(self, user_id: str) -> Any:
        """PUT creates or regenerates the token (old links stop working)."""
        return self._send("PUT", f"/api/v2/users/{user_id}/reset_password_tokens",
                          idempotent=False)

    def get_role(self, role_id: str) -> Any:
        return self._get(f"/api/v1/roles/{role_id}")

    def create_role(self, role: dict[str, Any]) -> Any:
        return self._post("/api/v1/roles", json_body=role, idempotent=False)

    def update_role(self, role_id: str, role: dict[str, Any]) -> Any:
        return self._send("PUT", f"/api/v1/roles/{role_id}", json_body=role)

    def delete_role(self, role_id: str) -> Any:
        return self._send("DELETE", f"/api/v1/roles/{role_id}")

    def list_role_users(self, role_id: str) -> Any:
        return self._get(f"/api/v2/roles/{role_id}/users")

    # ------------------------------------------------------------------
    # Clone / lock / public sharing
    # ------------------------------------------------------------------

    def clone_rule(self, rule_id: str) -> Any:
        """POST /api/v2/rules/{id}/clone (SigNoz 0.147+)."""
        return self._post(f"/api/v2/rules/{rule_id}/clone", idempotent=False)

    def clone_dashboard_v2(self, dashboard_id: str) -> Any:
        return self._post(f"/api/v2/dashboards/{dashboard_id}/clone", idempotent=False)

    def lock_dashboard_v2(self, dashboard_id: str) -> Any:
        return self._send("PUT", f"/api/v2/dashboards/{dashboard_id}/lock")

    def unlock_dashboard_v2(self, dashboard_id: str) -> Any:
        return self._send("DELETE", f"/api/v2/dashboards/{dashboard_id}/lock")

    def get_public_dashboard(self, dashboard_id: str) -> Any:
        return self._get(f"/api/v1/dashboards/{dashboard_id}/public")

    def create_public_dashboard(
        self, dashboard_id: str, time_range_enabled: bool, default_time_range: str
    ) -> Any:
        return self._post(f"/api/v1/dashboards/{dashboard_id}/public",
                          json_body={"timeRangeEnabled": time_range_enabled,
                                     "defaultTimeRange": default_time_range},
                          idempotent=False)

    def update_public_dashboard(
        self, dashboard_id: str, time_range_enabled: bool, default_time_range: str
    ) -> Any:
        return self._send("PUT", f"/api/v1/dashboards/{dashboard_id}/public",
                          json_body={"timeRangeEnabled": time_range_enabled,
                                     "defaultTimeRange": default_time_range})

    def delete_public_dashboard(self, dashboard_id: str) -> Any:
        return self._send("DELETE", f"/api/v1/dashboards/{dashboard_id}/public")

    # ------------------------------------------------------------------
    # Trace summary / thread (0.147+)
    # ------------------------------------------------------------------

    def get_trace_summary(self, trace_id: str) -> Any:
        return self._get(f"/api/v1/traces/{trace_id}/summary")

    def get_trace_thread(
        self, trace_id: str, limit: int | None = None, after: str = "",
        before: str = "", span_id: str = "",
    ) -> Any:
        params: dict[str, str] = {}
        if limit:
            params["limit"] = str(limit)
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if span_id:
            params["spanId"] = span_id
        return self._get(f"/api/v1/traces/{trace_id}/thread", params=params or None)

    # ------------------------------------------------------------------
    # Health (unauthenticated probes)
    # ------------------------------------------------------------------

    def get_health(self, probe: str = "readyz") -> tuple[int, Any]:
        """GET /api/v2/{healthz,livez,readyz}; returns (status_code, body).

        An unhealthy server answers 503 with the same body, so this does
        not raise on error statuses: the caller decides from both.
        """
        if probe not in ("healthz", "livez", "readyz"):
            raise ValueError(f"unknown probe {probe!r}")
        resp = self._request("GET", f"/api/v2/{probe}")
        try:
            body = resp.json()
        except ValueError:
            if resp.is_error:
                raise SigNozApiError(resp.status_code, _extract_error(resp)) from None
            raise SigNozApiError(
                resp.status_code,
                "unexpected non-JSON response; this API may not exist on the server version",
            ) from None
        return resp.status_code, body

    # ------------------------------------------------------------------
    # Infra monitoring
    # ------------------------------------------------------------------

    def list_infra(self, kind: str, payload: dict[str, Any]) -> Any:
        """POST /api/v2/infra_monitoring/{kind} (hosts, pods, nodes, ...)."""
        return self._post(f"/api/v2/infra_monitoring/{kind}", json_body=payload)

    def get_infra_checks(self, check_type: str) -> Any:
        return self._get("/api/v2/infra_monitoring/checks", params={"type": check_type})

    # ------------------------------------------------------------------
    # Ingestion keys and limits (SigNoz Cloud gateway)
    # ------------------------------------------------------------------

    def list_ingestion_keys(self, page: int = 1, per_page: int = 50, name: str = "") -> Any:
        params = {"page": str(page), "per_page": str(per_page)}
        if name:
            params["name"] = name
            return self._get("/api/v2/gateway/ingestion_keys/search", params=params)
        return self._get("/api/v2/gateway/ingestion_keys", params=params)

    def get_ingestion_key(self, key_id: str) -> Any:
        return self._get(f"/api/v2/gateway/ingestion_keys/{key_id}")

    def create_ingestion_key(self, key: dict[str, Any]) -> Any:
        return self._post("/api/v2/gateway/ingestion_keys", json_body=key, idempotent=False)

    def update_ingestion_key(self, key_id: str, key: dict[str, Any]) -> Any:
        return self._send("PATCH", f"/api/v2/gateway/ingestion_keys/{key_id}", json_body=key)

    def delete_ingestion_key(self, key_id: str) -> Any:
        return self._send("DELETE", f"/api/v2/gateway/ingestion_keys/{key_id}")

    def list_ingestion_key_limits(self, key_id: str) -> Any:
        return self._get(f"/api/v2/gateway/ingestion_keys/{key_id}/limits")

    def create_ingestion_limit(self, limit: dict[str, Any]) -> Any:
        """POST /api/v2/gateway/ingestion_limits (body carries keyId; 0.141+)."""
        return self._post("/api/v2/gateway/ingestion_limits", json_body=limit,
                          idempotent=False)

    def get_ingestion_limit(self, limit_id: str) -> Any:
        return self._get(f"/api/v2/gateway/ingestion_limits/{limit_id}")

    def update_ingestion_limit(self, limit_id: str, limit: dict[str, Any]) -> Any:
        return self._send("PATCH", f"/api/v2/gateway/ingestion_limits/{limit_id}",
                          json_body=limit)

    def delete_ingestion_limit(self, limit_id: str) -> Any:
        return self._send("DELETE", f"/api/v2/gateway/ingestion_limits/{limit_id}")

    # ------------------------------------------------------------------
    # Metrics explorer and reduction rules
    # ------------------------------------------------------------------

    def inspect_metric(
        self, metric_name: str, start: int, end: int, filter_expression: str = ""
    ) -> Any:
        body: dict[str, Any] = {"metricName": metric_name, "start": start, "end": end}
        if filter_expression:
            body["filter"] = {"expression": filter_expression}
        return self._post("/api/v2/metrics/inspect", json_body=body)

    def get_metrics_treemap(
        self, start: int, end: int, mode: str = "timeseries", limit: int = 20,
        filter_expression: str = "",
    ) -> Any:
        body: dict[str, Any] = {"start": start, "end": end, "mode": mode, "limit": limit}
        if filter_expression:
            body["filter"] = {"expression": filter_expression}
        return self._post("/api/v2/metrics/treemap", json_body=body)

    def list_reduction_rules(self, **params: Any) -> Any:
        clean = {k: str(v) for k, v in params.items() if v not in (None, "")}
        return self._get("/api/v2/metric_reduction_rules", params=clean or None)

    def get_reduction_rule(self, rule_id: str) -> Any:
        return self._get(f"/api/v2/metric_reduction_rules/{rule_id}")

    def create_reduction_rule(self, rule: dict[str, Any]) -> Any:
        return self._post("/api/v2/metric_reduction_rules", json_body=rule, idempotent=False)

    def update_reduction_rule(self, rule_id: str, rule: dict[str, Any]) -> Any:
        return self._send("PUT", f"/api/v2/metric_reduction_rules/{rule_id}", json_body=rule)

    def delete_reduction_rule(self, rule_id: str) -> Any:
        return self._send("DELETE", f"/api/v2/metric_reduction_rules/{rule_id}")

    def preview_reduction_rule(self, rule: dict[str, Any]) -> Any:
        return self._post("/api/v2/metric_reduction_rules/preview", json_body=rule)

    def get_reduction_stats(self) -> Any:
        return self._get("/api/v2/metric_reduction_rules/stats")

    # ------------------------------------------------------------------
    # Promoted paths (0.147+)
    # ------------------------------------------------------------------

    def list_promoted_paths(self, **filters: Any) -> Any:
        params: dict[str, str] = {}
        for key, value in filters.items():
            if value is None or value == "":
                continue
            params[key] = str(value).lower() if isinstance(value, bool) else str(value)
        return self._get("/api/v1/promoted_paths", params=params or None)

    def promote_paths(self, paths: list[dict[str, Any]]) -> Any:
        return self._send("POST", "/api/v1/promoted_paths", json_body=paths)

    def index_materialized_paths(self, signal: str, dry_run: bool = False) -> Any:
        return self._post("/api/v1/promoted_paths/materialized",
                          json_body={"signal": signal, "dryRun": dry_run},
                          idempotent=dry_run)

    # ------------------------------------------------------------------
    # Route policies, rule/dashboard saved views, quick filters
    # ------------------------------------------------------------------

    def list_route_policies(self) -> Any:
        return self._get("/api/v1/route_policies")

    def get_route_policy(self, policy_id: str) -> Any:
        return self._get(f"/api/v1/route_policies/{policy_id}")

    def create_route_policy(self, policy: dict[str, Any]) -> Any:
        return self._post("/api/v1/route_policies", json_body=policy, idempotent=False)

    def update_route_policy(self, policy_id: str, policy: dict[str, Any]) -> Any:
        return self._send("PUT", f"/api/v1/route_policies/{policy_id}", json_body=policy)

    def delete_route_policy(self, policy_id: str) -> Any:
        return self._send("DELETE", f"/api/v1/route_policies/{policy_id}")

    def list_rule_views(self) -> Any:
        """GET /api/v2/rule_views (0.147+)."""
        return self._get("/api/v2/rule_views")

    def create_rule_view(self, view: dict[str, Any]) -> Any:
        return self._post("/api/v2/rule_views", json_body=view, idempotent=False)

    def update_rule_view(self, view_id: str, view: dict[str, Any]) -> Any:
        return self._send("PUT", f"/api/v2/rule_views/{view_id}", json_body=view)

    def delete_rule_view(self, view_id: str) -> Any:
        return self._send("DELETE", f"/api/v2/rule_views/{view_id}")

    def list_dashboard_views(self) -> Any:
        return self._get("/api/v2/dashboard_views")

    def create_dashboard_view(self, view: dict[str, Any]) -> Any:
        return self._post("/api/v2/dashboard_views", json_body=view, idempotent=False)

    def update_dashboard_view(self, view_id: str, view: dict[str, Any]) -> Any:
        return self._send("PUT", f"/api/v2/dashboard_views/{view_id}", json_body=view)

    def delete_dashboard_view(self, view_id: str) -> Any:
        return self._send("DELETE", f"/api/v2/dashboard_views/{view_id}")

    def list_quick_filters(self) -> Any:
        """GET /api/v2/quick_filters (0.141+)."""
        return self._get("/api/v2/quick_filters")

    def get_quick_filters(self, source: str) -> Any:
        return self._get(f"/api/v2/quick_filters/{source}")

    def set_quick_filters(self, source: str, filters: list[dict[str, Any]]) -> Any:
        """PUT replaces the whole filter list for a source."""
        return self._send("PUT", f"/api/v2/quick_filters/{source}",
                          json_body={"filters": filters})

    # ------------------------------------------------------------------
    # Cloud integrations (AWS / Azure / GCP)
    # ------------------------------------------------------------------

    def list_cloud_accounts(self, provider: str) -> Any:
        return self._get(f"/api/v1/cloud_integrations/{provider}/accounts")

    def get_cloud_account(self, provider: str, account_id: str) -> Any:
        return self._get(f"/api/v1/cloud_integrations/{provider}/accounts/{account_id}")

    def create_cloud_account(self, provider: str, config: dict[str, Any],
                             credentials: dict[str, Any]) -> Any:
        """Returns the account id plus the connection artifact (e.g. an AWS
        CloudFormation URL) to finish the connection on the provider side.

        *credentials* (SigNoz API/ingestion URLs and keys the provider-side
        stack uses) is required; `get_cloud_credentials` supplies it.
        """
        return self._post(f"/api/v1/cloud_integrations/{provider}/accounts",
                          json_body={"config": config, "credentials": credentials},
                          idempotent=False)

    def update_cloud_account(self, provider: str, account_id: str,
                             config: dict[str, Any]) -> Any:
        return self._send("PUT",
                          f"/api/v1/cloud_integrations/{provider}/accounts/{account_id}",
                          json_body={"config": config})

    def delete_cloud_account(self, provider: str, account_id: str) -> Any:
        return self._send("DELETE",
                          f"/api/v1/cloud_integrations/{provider}/accounts/{account_id}")

    def list_cloud_services(self, provider: str, account_id: str = "") -> Any:
        if account_id:
            return self._get(
                f"/api/v1/cloud_integrations/{provider}/accounts/{account_id}/services")
        return self._get(f"/api/v1/cloud_integrations/{provider}/services")

    def get_cloud_service(self, provider: str, service_id: str, account_id: str = "") -> Any:
        if account_id:
            return self._get(f"/api/v1/cloud_integrations/{provider}/accounts/"
                             f"{account_id}/services/{service_id}")
        return self._get(f"/api/v1/cloud_integrations/{provider}/services/{service_id}")

    def update_cloud_service(self, provider: str, account_id: str, service_id: str,
                             config: dict[str, Any]) -> Any:
        return self._send("PUT", f"/api/v1/cloud_integrations/{provider}/accounts/"
                                 f"{account_id}/services/{service_id}",
                          json_body={"config": config})

    def get_cloud_credentials(self, provider: str) -> Any:
        return self._get(f"/api/v1/cloud_integrations/{provider}/credentials")

    # ------------------------------------------------------------------
    # Raw data export (streamed)
    # ------------------------------------------------------------------

    def export_raw_data(self, payload: dict[str, Any], out: Any, fmt: str = "csv") -> int:
        """Stream POST /api/v1/export_raw_data into the binary file *out*.

        Returns the number of bytes written. Not retried: the response is
        a stream, and a partial write cannot be resumed.
        """
        if fmt not in ("csv", "jsonl"):
            raise ValueError(f"unknown export format {fmt!r}")
        written = 0
        with self._client.stream(
            "POST", f"{self.endpoint}/api/v1/export_raw_data",
            params={"format": fmt}, json=payload,
        ) as resp:
            if resp.is_error:
                resp.read()
                raise SigNozApiError(resp.status_code, _extract_error(resp))
            if resp.is_redirect:
                raise SigNozApiError(
                    resp.status_code,
                    f"unexpected redirect to {resp.headers.get('location', '?')} "
                    "(check the endpoint URL, e.g. http vs https)",
                )
            ctype = resp.headers.get("content-type", "")
            if ctype.startswith("text/html"):
                raise SigNozApiError(
                    resp.status_code,
                    "unexpected HTML response; this API may not exist on the server version",
                )
            for chunk in resp.iter_bytes():
                out.write(chunk)
                written += len(chunk)
        return written

    def _send(self, method: str, path: str, json_body: Any = None,
              idempotent: bool = True) -> Any:
        """Request whose success may be an empty 204 (normalized to status ok)."""
        resp = self._request(method, path, json_body=json_body, idempotent=idempotent)
        return self._parse(resp, allow_empty=True)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        idempotent: bool = True,
    ) -> httpx.Response:
        """Send a request with retry/backoff on 429, transient 5xx, and network errors.

        Retrying is only safe when the outcome is known-untouched: 429 means
        the server explicitly rejected the request, so even non-idempotent
        calls may retry those. Transport errors and 5xx leave the outcome
        ambiguous (the server may have committed before failing), so they
        are retried only for idempotent calls. Callers performing creates
        or patches must pass ``idempotent=False`` to avoid duplicates.
        """
        attempt = 0
        while True:
            try:
                resp = self._client.request(
                    method, f"{self.endpoint}{path}", params=params, json=json_body
                )
            except httpx.TransportError:
                # Connection reset / DNS / read timeout etc. -- outcome unknown.
                if idempotent and attempt < self.max_retries:
                    time.sleep(min(2**attempt, 30))
                    attempt += 1
                    continue
                raise
            if resp.status_code in _RETRYABLE and attempt < self.max_retries:
                if resp.status_code != 429 and not idempotent:
                    return resp
                delay = min(2**attempt, 30)
                if resp.status_code == 429:
                    retry_after = _parse_retry_after(resp.headers.get("Retry-After", ""))
                    if retry_after is not None:
                        delay = max(delay, min(retry_after, 60))
                time.sleep(delay)
                attempt += 1
                continue
            return resp

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse(self, resp: httpx.Response, allow_empty: bool = False) -> Any:
        if resp.is_error:
            raise SigNozApiError(resp.status_code, _extract_error(resp))
        if resp.is_redirect:
            # The API never redirects. Redirects come from the router on
            # malformed paths (an empty id: /rules//clone -> 301) or from a
            # proxy (http -> https); unhandled, they read as empty success.
            raise SigNozApiError(
                resp.status_code,
                f"unexpected redirect to {resp.headers.get('location', '?')} "
                "(check the endpoint URL, e.g. http vs https, and any id arguments)",
            )
        if allow_empty and (not resp.content or resp.content.strip() in (b"", b"null")):
            return {"status": "ok"}
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            # A 200 with a non-JSON body is almost always the frontend
            # fallback page: the API path does not exist on this server
            # version (seen with saved-views on v0.136). Fail loudly
            # instead of rendering HTML as table rows.
            ctype = resp.headers.get("content-type", "unknown")
            raise SigNozApiError(
                resp.status_code,
                f"unexpected non-JSON response (content-type: {ctype}); "
                "this API may not exist on the server version",
            ) from None

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self._parse(self._request("GET", path, params=params))

    def _post(self, path: str, json_body: Any = None, idempotent: bool = True) -> Any:
        return self._parse(
            self._request("POST", path, json_body=json_body, idempotent=idempotent)
        )

    def _put(self, path: str, json_body: Any = None, idempotent: bool = True) -> Any:
        return self._parse(
            self._request("PUT", path, json_body=json_body, idempotent=idempotent)
        )

    def _patch(self, path: str, json_body: Any = None, idempotent: bool = True) -> Any:
        return self._parse(
            self._request("PATCH", path, json_body=json_body, idempotent=idempotent)
        )


def _extract_error(resp: httpx.Response) -> str:
    """Pull a human-readable message out of a SigNoz error response.

    The message is server-controlled: strip terminal control bytes so
    hostile error text cannot inject escape sequences into the caller's
    terminal when the error is reported on stderr.
    """
    from signoz_cli.output import _safe_plain

    try:
        body = resp.json()
    except ValueError:
        return _safe_plain(resp.text[:500])

    # SigNoz error envelope: {"error": {"code": ..., "message": ...}} or {"message": ...}
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code", "")
            msg = err.get("message", "")
            # The specific reasons ("notificationSettings: field is
            # required") live in error.errors; the message alone is generic.
            details = [
                str(e.get("message")) for e in err.get("errors") or []
                if isinstance(e, dict) and e.get("message")
            ]
            if details:
                msg = f"{msg}: {'; '.join(details)}" if msg else "; ".join(details)
            return _safe_plain(f"{code}: {msg}" if code else msg)
        for key in ("message", "error", "detail"):
            if key in body:
                return _safe_plain(str(body[key]))
    return _safe_plain(str(body)[:500])
