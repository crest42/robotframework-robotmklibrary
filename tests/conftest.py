"""Isolation shared by the unit and integration tests."""

from collections.abc import Iterator

import pytest
from opentelemetry.test.globals_test import reset_logging_globals, reset_trace_globals


@pytest.fixture(autouse=True)
def _reset_opentelemetry_globals() -> Iterator[None]:
    # The listener registers its providers as process globals, which the SDK accepts once.
    yield
    reset_trace_globals()
    reset_logging_globals()


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "CHECKMK_RESOURCE_ATTRIBUTES",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_SERVICE_NAME",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        monkeypatch.delenv(variable, raising=False)
