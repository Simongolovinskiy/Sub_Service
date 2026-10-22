from uuid import uuid4

from fastapi import Request
from opentelemetry import trace
from opentelemetry.propagate import inject
from starlette.middleware.base import BaseHTTPMiddleware

from app.infrastructure.adapters._logging.context import set_request_id


class TracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("x-request-id") or str(uuid4())

        set_request_id(req_id)

        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span(
            name=f"{request.method} {request.url.path}",
            attributes={
                "http.method": request.method,
                "http.route": request.url.path,
                "http.url": str(request.url),
                "request.id": req_id,
            }
        ) as span:
            # 3. trace context Propagation
            headers = {}
            inject(headers)  # traceparent, tracestate e.t.c

            headers["x-request-id"] = req_id

            response = await call_next(request)

            response.headers["x-request-id"] = req_id

            if response.status_code >= 400:
                span.record_exception(Exception(f"HTTP {response.status_code}"))
                span.set_attribute("http.status_code", response.status_code)

            return response
