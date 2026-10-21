import contextvars
from uuid import uuid4

from opentelemetry import trace

from app.domain.common.constants import SERVICE_NAME

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="n/a")


def get_request_id() -> str:
    rid = request_id_var.get()
    if rid == "n/a":
        rid = str(uuid4())
        request_id_var.set(rid)
    return rid


def set_request_id(value: str) -> None:
    request_id_var.set(value)


def enrich(record):
    record["extra"]["service"] = SERVICE_NAME
    record["extra"]["request_id"] = get_request_id()

    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        ctx = span.get_span_context()
        record["extra"]["trace_id"] = f"{ctx.trace_id:032x}"
        record["extra"]["span_id"] = f"{ctx.span_id:016x}"
        record["extra"]["trace_flags"] = f"{ctx.trace_flags:02x}"

    return record
