"""Seed deterministic e2e telemetry into a live SigNoz instance via OTLP.

Used by the live integration tests so assertions run against real,
freshly-ingested rows instead of whatever happens to be in the backend:

    python tests/seed_live_data.py --otlp-grpc localhost:4317 --run-id abc123

Sends one trace (service ``signoz-cli-e2e``, operation
``e2e-operation-<run-id>``) and one log record whose body embeds the run id,
both tagged with ``e2e.run.id=<run-id>``. Exits 0 once the exporters flush.

No global OpenTelemetry provider state is touched, so the function can be
called repeatedly in one process (e.g. seeding both the v0.138 and the
v0.136 instance from a single test session).
"""

from __future__ import annotations

import argparse
import time

SERVICE_NAME = "signoz-cli-e2e"
OPERATION_PREFIX = "e2e-operation"
RUN_ATTR = "e2e.run.id"


def operation_name(run_id: str) -> str:
    return f"{OPERATION_PREFIX}-{run_id}"


def log_body(run_id: str) -> str:
    return f"e2e log marker run {run_id}"


def seed(otlp_grpc: str, run_id: str) -> dict[str, str]:
    """Export one span + one log record tagged with *run_id*."""
    from opentelemetry._logs import SeverityNumber
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
        OTLPLogExporter,
    )
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": SERVICE_NAME})
    op_name = operation_name(run_id)
    body = log_body(run_id)

    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"http://{otlp_grpc}", insecure=True),
            schedule_delay_millis=200,
        )
    )
    tracer = trace_provider.get_tracer("signoz-cli-e2e-seeder")
    with tracer.start_as_current_span(
        op_name, attributes={RUN_ATTR: run_id}
    ) as span:
        trace_id = format(span.get_span_context().trace_id, "032x")
        time.sleep(0.05)
    trace_provider.force_flush(timeout_millis=15_000)
    trace_provider.shutdown()

    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=f"http://{otlp_grpc}", insecure=True),
            schedule_delay_millis=200,
        )
    )
    log_provider.get_logger("signoz-cli-e2e-seeder").emit(
        body=body,
        severity_number=SeverityNumber.INFO,
        severity_text="INFO",
        attributes={RUN_ATTR: run_id},
    )
    log_provider.force_flush(timeout_millis=15_000)
    log_provider.shutdown()

    return {"trace_id": trace_id, "operation": op_name, "log_body": body}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--otlp-grpc", default="localhost:4317")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = seed(args.otlp_grpc, args.run_id)
    print(f"seeded run {args.run_id}: trace {result['trace_id']}")


if __name__ == "__main__":
    main()
