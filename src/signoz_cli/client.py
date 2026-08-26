"""HTTP client for the SigNoz API (targets SigNoz 0.136+ / 0.138+)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from signoz_cli.query import build_promql_query_payload, build_sql_query_payload

_RETRYABLE = {429, 500, 502, 503, 504}


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
        return self._post("/api/v2/dashboards", json_body=dashboard)

    def update_dashboard_v2(self, dashboard_id: str, dashboard: dict[str, Any]) -> Any:
        return self._put(f"/api/v2/dashboards/{dashboard_id}", json_body=dashboard)

    def patch_dashboard_v2(self, dashboard_id: str, patches: list[dict[str, Any]]) -> Any:
        return self._patch(f"/api/v2/dashboards/{dashboard_id}", json_body=patches)

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

    # ------------------------------------------------------------------
    # Alert rule management (v2)
    # ------------------------------------------------------------------

    def create_rule(self, rule: dict[str, Any]) -> Any:
        return self._post("/api/v2/rules", json_body=rule)

    def update_rule(self, rule_id: str, rule: dict[str, Any]) -> Any:
        return self._put(f"/api/v2/rules/{rule_id}", json_body=rule)

    def patch_rule(self, rule_id: str, patches: list[dict[str, Any]]) -> Any:
        return self._patch(f"/api/v2/rules/{rule_id}", json_body=patches)

    def delete_rule(self, rule_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v2/rules/{rule_id}")
        return self._parse(resp, allow_empty=True)

    def test_rule(self, rule: dict[str, Any]) -> Any:
        return self._post("/api/v2/rules/test", json_body=rule)

    # ------------------------------------------------------------------
    # Notification channels (admin)
    # ------------------------------------------------------------------

    def list_channels(self) -> Any:
        return self._get("/api/v1/channels")

    def get_channel(self, channel_id: str) -> Any:
        return self._get(f"/api/v1/channels/{channel_id}")

    def create_channel(self, channel: dict[str, Any]) -> Any:
        return self._post("/api/v1/channels", json_body=channel)

    def update_channel(self, channel_id: str, channel: dict[str, Any]) -> Any:
        return self._put(f"/api/v1/channels/{channel_id}", json_body=channel)

    def delete_channel(self, channel_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v1/channels/{channel_id}")
        return self._parse(resp, allow_empty=True)

    # ------------------------------------------------------------------
    # Saved views (0.137+ spec-based API)
    # ------------------------------------------------------------------

    def list_saved_views(self, source_page: str = "logs") -> Any:
        return self._get("/api/v1/saved_views", params={"sourcePage": source_page})

    def get_saved_view(self, view_id: str) -> Any:
        return self._get(f"/api/v1/saved_views/{view_id}")

    def create_saved_view(self, view: dict[str, Any]) -> Any:
        return self._post("/api/v1/saved_views", json_body=view)

    def delete_saved_view(self, view_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v1/saved_views/{view_id}")
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
        data = self._post("/api/v1/service_accounts", json_body={"name": name})
        failed_roles: list[str] = []
        if role_ids and isinstance(data, dict):
            new_id = (data.get("data") or {}).get("id")
            if new_id:
                for rid in role_ids:
                    try:
                        self._post(
                            f"/api/v1/service_accounts/{new_id}/roles",
                            json_body={"id": rid},
                        )
                    except SigNozApiError as exc:
                        failed_roles.append(f"{rid}: {exc.message}")
        return data, failed_roles

    def delete_service_account(self, account_id: str) -> Any:
        resp = self._request("DELETE", f"/api/v1/service_accounts/{account_id}")
        return self._parse(resp, allow_empty=True)

    def list_roles(self) -> Any:
        return self._get("/api/v1/roles")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> httpx.Response:
        """Send a request with retry/backoff on 429 and transient 5xx."""
        attempt = 0
        while True:
            resp = self._client.request(
                method, f"{self.endpoint}{path}", params=params, json=json_body
            )
            if resp.status_code in _RETRYABLE and attempt < self.max_retries:
                delay = min(2**attempt, 30)
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        delay = max(delay, min(int(retry_after), 60))
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
        if allow_empty and (not resp.content or resp.content.strip() in (b"", b"null")):
            return {"status": "ok"}
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self._parse(self._request("GET", path, params=params))

    def _post(self, path: str, json_body: Any = None) -> Any:
        return self._parse(self._request("POST", path, json_body=json_body))

    def _put(self, path: str, json_body: Any = None) -> Any:
        return self._parse(self._request("PUT", path, json_body=json_body))

    def _patch(self, path: str, json_body: Any = None) -> Any:
        return self._parse(self._request("PATCH", path, json_body=json_body))


def _extract_error(resp: httpx.Response) -> str:
    """Pull a human-readable message out of a SigNoz error response."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]

    # SigNoz error envelope: {"error": {"code": ..., "message": ...}} or {"message": ...}
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code", "")
            msg = err.get("message", "")
            return f"{code}: {msg}" if code else msg
        for key in ("message", "error", "detail"):
            if key in body:
                return str(body[key])
    return str(body)[:500]
