"""Library keyword that opens a span through the global API, as other instrumentations do."""

from opentelemetry import trace


def open_a_span() -> None:
    with trace.get_tracer("other-instrumentation").start_as_current_span(
        "http.request"
    ):
        pass
