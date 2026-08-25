"""Query Builder v5 payload constructors mirroring the Go reference implementation."""

from __future__ import annotations

from typing import Any


def build_logs_query_payload(
    start: int,
    end: int,
    filter_expression: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return {
        "schemaVersion": "v1",
        "start": start,
        "end": end,
        "requestType": "raw",
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": "logs",
                        "disabled": False,
                        "filter": {"expression": filter_expression},
                        "limit": limit,
                        "offset": offset,
                        "order": [{"key": {"name": "timestamp"}, "direction": "desc"}],
                        "having": {"expression": ""},
                        "selectFields": [
                            {
                                "name": "timestamp",
                                "fieldDataType": "string",
                                "signal": "logs",
                            },
                            {
                                "name": "severity_text",
                                "fieldDataType": "string",
                                "signal": "logs",
                            },
                            {
                                "name": "body",
                                "fieldDataType": "string",
                                "signal": "logs",
                            },
                            {
                                "name": "service.name",
                                "fieldDataType": "string",
                                "signal": "logs",
                                "fieldContext": "resource",
                            },
                        ],
                    },
                }
            ]
        },
        "formatOptions": {"formatTableResultForUI": False, "fillGaps": False},
        "variables": {},
    }


def build_traces_query_payload(
    start: int,
    end: int,
    filter_expression: str,
    limit: int = 1000,
) -> dict[str, Any]:
    return {
        "schemaVersion": "v1",
        "start": start,
        "end": end,
        "requestType": "raw",
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": "traces",
                        "disabled": False,
                        "filter": {"expression": filter_expression},
                        "limit": limit,
                        "offset": 0,
                        "order": [{"key": {"name": "timestamp"}, "direction": "desc"}],
                        "having": {"expression": ""},
                        "selectFields": [
                            {
                                "name": "traceID",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "spanID",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "parentSpanID",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "service.name",
                                "fieldDataType": "string",
                                "signal": "traces",
                                "fieldContext": "resource",
                            },
                            {
                                "name": "name",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "durationNano",
                                "fieldDataType": "int64",
                                "signal": "traces",
                            },
                            {
                                "name": "timestamp",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "hasError",
                                "fieldDataType": "bool",
                                "signal": "traces",
                            },
                            {
                                "name": "statusCode",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "statusCodeString",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "httpMethod",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "httpUrl",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "spanKind",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "rpcMethod",
                                "fieldDataType": "string",
                                "signal": "traces",
                            },
                            {
                                "name": "kind",
                                "fieldDataType": "int32",
                                "signal": "traces",
                            },
                        ],
                    },
                }
            ]
        },
        "formatOptions": {"formatTableResultForUI": False, "fillGaps": False},
        "variables": {},
    }


def build_aggregate_query_payload(
    signal: str,
    start: int,
    end: int,
    aggregation_expr: str,
    filter_expression: str,
    group_by: list[dict[str, str]],
    order_expr: str,
    order_dir: str,
    limit: int = 10,
) -> dict[str, Any]:
    return {
        "schemaVersion": "v1",
        "start": start,
        "end": end,
        "requestType": "scalar",
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": signal,
                        "disabled": False,
                        "filter": {"expression": filter_expression},
                        "limit": limit,
                        "offset": 0,
                        "order": [
                            {"key": {"name": order_expr}, "direction": order_dir}
                        ],
                        "having": {"expression": ""},
                        "groupBy": group_by,
                        "aggregations": [{"expression": aggregation_expr}],
                    },
                }
            ]
        },
        "formatOptions": {"formatTableResultForUI": False, "fillGaps": False},
        "variables": {},
    }


def build_metrics_query_payload(
    start: int,
    end: int,
    metric_name: str,
    temporality: str = "Unspecified",
    time_aggregation: str = "avg",
    space_aggregation: str = "sum",
    group_by: list[str] | None = None,
    filter_expression: str = "",
    step_interval: int = 60,
) -> dict[str, Any]:
    """Time-series aggregation over one metric (Query Builder v5)."""
    return {
        "schemaVersion": "v1",
        "start": start,
        "end": end,
        "requestType": "time_series",
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": "metrics",
                        "stepInterval": step_interval,
                        "disabled": False,
                        "aggregations": [
                            {
                                "metricName": metric_name,
                                "temporality": temporality,
                                "timeAggregation": time_aggregation,
                                "spaceAggregation": space_aggregation,
                            }
                        ],
                        "filter": {"expression": filter_expression},
                        "groupBy": [
                            {"name": g, "signal": "metrics"} for g in (group_by or [])
                        ],
                    },
                }
            ]
        },
        "formatOptions": {"formatTableResultForUI": False, "fillGaps": False},
        "variables": {},
    }


def build_promql_query_payload(
    start: int,
    end: int,
    query: str,
    step_interval: int = 60,
) -> dict[str, Any]:
    return {
        "schemaVersion": "v1",
        "start": start,
        "end": end,
        "requestType": "time_series",
        "compositeQuery": {
            "queries": [
                {
                    "type": "promql",
                    "spec": {"name": "A", "query": query, "stepInterval": step_interval},
                }
            ],
        },
        "formatOptions": {"formatTableResultForUI": False, "fillGaps": False},
        "variables": {},
    }


def build_sql_query_payload(start: int, end: int, query: str) -> dict[str, Any]:
    return {
        "schemaVersion": "v1",
        "start": start,
        "end": end,
        "requestType": "raw",
        "compositeQuery": {
            "queries": [
                {
                    "type": "clickhouse_sql",
                    "spec": {"name": "A", "query": query, "disabled": False},
                }
            ],
        },
        "formatOptions": {"formatTableResultForUI": False, "fillGaps": False},
        "variables": {},
    }
