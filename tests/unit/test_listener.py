"""The listener hooks called directly with stand-ins for the Robot Framework objects."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Unpack

import pytest
from opentelemetry import trace
from opentelemetry.sdk._logs import ReadableLogRecord
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util.types import AttributeValue

from RobotmkLibrary import __version__
from RobotmkLibrary.instrumentation._exceptions import (
    GlobalLoggerProviderAlreadySetError,
    GlobalTracerProviderAlreadySetError,
)
from RobotmkLibrary.instrumentation._logs import LoggerProvider, set_logger_provider
from RobotmkLibrary.instrumentation._protocols import StatusResult
from RobotmkLibrary.instrumentation.listener import RobotmkListener
from tests.helpers import ListenerOptions, attributes, scope

START = datetime(2026, 11, 17, 10, 0, 0, 250_000, tzinfo=UTC)
END = datetime(2026, 11, 17, 10, 0, 1, 500_000, tzinfo=UTC)
START_NS = int(START.timestamp()) * 1_000_000_000 + START.microsecond * 1_000
END_NS = int(END.timestamp()) * 1_000_000_000 + END.microsecond * 1_000
PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
SELENIUM_SCREENSHOT = (
    '</td></tr><tr><td colspan="3"><img alt="screenshot" class="robot-seleniumlibrary-screenshot" '
    f'src="data:image/png;base64,{PNG}" width="800px">'
)
BROWSER_SCREENSHOT = (
    '</td></tr><tr><td colspan="3"><img alt="screenshot" class="robot-seleniumlibrary-screenshot" '
    f'style="max-width:900px;max-height:900px;" src="data:image/png;base64,{PNG}"/>'
)
FILE_SCREENSHOT = '</td></tr><tr><td colspan="3"><a href="shot.png"><img src="shot.png" width="800px"></a>'
PNG_LOG_ATTRIBUTES = {
    "rf.log.level": "INFO",
    "rf.log.html": "false",
    "rf.log.image": "image/png",
}
TEXT_LOG_ATTRIBUTES = {"rf.log.level": "INFO", "rf.log.html": "false"}


@dataclass
class Result:
    """Stand-in for the suite, test, keyword and body item results of Robot Framework."""

    name: str = "Demo"
    full_name: str = ""
    status: str = "PASS"
    message: str = ""
    start_time: datetime | None = START
    end_time: datetime | None = END
    type: str = "KEYWORD"
    args: Sequence[object] = ()

    def __post_init__(self) -> None:
        self.full_name = self.full_name or self.name


@dataclass
class Message:
    """Stand-in for a Robot Framework log message."""

    message: str
    level: str = "INFO"
    html: bool = False
    timestamp: datetime | None = START


def _listener(
    **options: Unpack[ListenerOptions],
) -> tuple[RobotmkListener, InMemorySpanExporter, InMemoryLogRecordExporter]:
    spans = InMemorySpanExporter()
    logs = InMemoryLogRecordExporter()
    return (
        RobotmkListener(span_exporter=spans, log_exporter=logs, **options),
        spans,
        logs,
    )


def _finished(
    listener: RobotmkListener,
    spans: InMemorySpanExporter,
    logs: InMemoryLogRecordExporter,
) -> tuple[Sequence[ReadableSpan], Sequence[ReadableLogRecord]]:
    """Flush the batch processors and return what they exported."""
    listener.close()
    return spans.get_finished_spans(), logs.get_finished_logs()


def _records(
    items: Sequence[ReadableLogRecord],
) -> list[tuple[object, dict[str, object]]]:
    return [
        (item.log_record.body, dict(attributes(item.log_record.attributes)))
        for item in items
    ]


@pytest.mark.parametrize(
    ("start", "end", "result", "name", "expected"),
    [
        pytest.param(
            RobotmkListener.start_suite,
            RobotmkListener.end_suite,
            Result(name="Demo", status="FAIL"),
            "Demo",
            {"rf.type": "suite", "rf.suite.name": "Demo", "rf.status": "FAIL"},
            id="suite",
        ),
        pytest.param(
            RobotmkListener.start_test,
            RobotmkListener.end_test,
            Result(name="Passes", full_name="Demo.Passes"),
            "Passes",
            {
                "rf.type": "test",
                "rf.test.name": "Passes",
                "rf.test.full_name": "Demo.Passes",
                "rf.status": "PASS",
            },
            id="test",
        ),
        pytest.param(
            RobotmkListener.start_keyword,
            RobotmkListener.end_keyword,
            Result(
                name="Log", full_name="BuiltIn.Log", type="SETUP", args=("hi", "WARN")
            ),
            "BuiltIn.Log",
            {
                "rf.type": "keyword",
                "rf.keyword.name": "BuiltIn.Log",
                "rf.keyword.type": "SETUP",
                "rf.keyword.args": "hi    WARN",
                "rf.status": "PASS",
            },
            id="keyword",
        ),
    ],
)
def test_a_span_per_suite_test_and_keyword(
    start: Callable[[RobotmkListener, object, Result], None],
    end: Callable[[RobotmkListener, object, Result], None],
    result: Result,
    name: str,
    expected: dict[str, str],
) -> None:
    listener, spans, logs = _listener()

    start(listener, None, result)
    end(listener, None, result)

    (span,), _items = _finished(listener, spans, logs)
    assert span.name == name
    assert dict(attributes(span.attributes)) == expected
    assert span.kind.name == "INTERNAL"
    assert (span.start_time, span.end_time) == (START_NS, END_NS)


@pytest.mark.parametrize(
    ("status", "code", "description"),
    [
        pytest.param("PASS", "OK", None, id="pass"),
        pytest.param("FAIL", "ERROR", "500 != 200", id="fail"),
        pytest.param("SKIP", "UNSET", None, id="skip"),
        pytest.param("NOT RUN", "UNSET", None, id="not run"),
    ],
)
def test_the_robot_status_maps_to_the_span_status(
    status: str, code: str, description: str | None
) -> None:
    listener, spans, logs = _listener()
    result = Result(status=status, message="500 != 200")

    listener.start_test(None, result)
    listener.end_test(None, result)

    (span,), _items = _finished(listener, spans, logs)
    assert attributes(span.attributes)["rf.status"] == status
    assert span.status.status_code.name == code
    assert (span.status.description or None) == description


def test_spans_nest_in_hook_order_within_one_trace() -> None:
    listener, spans, _logs = _listener()
    suite, test, keyword = (
        Result(name="Demo"),
        Result(name="Passes"),
        Result(name="Log"),
    )

    listener.start_suite(None, suite)
    listener.start_test(None, test)
    listener.start_keyword(None, keyword)
    listener.end_keyword(None, keyword)
    listener.end_test(None, test)
    listener.end_suite(None, suite)

    listener.close()
    by_name = {span.name: span for span in spans.get_finished_spans()}
    assert by_name["Demo"].parent is None
    assert by_name["Passes"].parent is not None
    assert by_name["Passes"].parent.span_id == by_name["Demo"].context.span_id
    assert by_name["Log"].parent is not None
    assert by_name["Log"].parent.span_id == by_name["Passes"].context.span_id
    assert len({span.context.trace_id for span in by_name.values()}) == 1


def test_spans_and_records_carry_the_library_scope() -> None:
    listener, spans, logs = _listener()
    result = Result()

    listener.start_suite(None, result)
    listener.log_message(Message("hello"))
    listener.end_suite(None, result)

    (span,), (item,) = _finished(listener, spans, logs)
    assert (
        scope(span.instrumentation_scope).name,
        scope(span.instrumentation_scope).version,
    ) == (
        "RobotmkLibrary",
        __version__,
    )
    assert (
        scope(item.instrumentation_scope).name,
        scope(item.instrumentation_scope).version,
    ) == (
        "RobotmkLibrary",
        __version__,
    )


def test_control_structures_are_skipped_by_default_and_keep_the_nesting() -> None:
    listener, spans, _logs = _listener()
    test, loop, keyword = Result(name="T"), Result(type="FOR"), Result(name="Log")

    listener.start_test(None, test)
    listener.start_body_item(None, loop)
    listener.start_keyword(None, keyword)
    listener.end_keyword(None, keyword)
    listener.end_body_item(None, loop)
    listener.end_test(None, test)

    listener.close()
    by_name = {span.name: span for span in spans.get_finished_spans()}
    assert set(by_name) == {"T", "Log"}
    assert by_name["Log"].parent is not None
    assert by_name["Log"].parent.span_id == by_name["T"].context.span_id


def test_control_structures_become_spans_on_request() -> None:
    listener, spans, logs = _listener(export_control_structures=True)
    loop = Result(type="FOR", status="NOT RUN")

    listener.start_body_item(None, loop)
    listener.end_body_item(None, loop)

    (span,), _items = _finished(listener, spans, logs)
    assert span.name == "FOR"
    assert dict(attributes(span.attributes)) == {
        "rf.type": "control",
        "rf.control.type": "FOR",
        "rf.status": "NOT RUN",
    }


class _FailingOnGreet(RobotmkListener):
    def _start_span(
        self, name: str, attributes: Mapping[str, AttributeValue], result: StatusResult
    ) -> None:
        if name == "Greet":
            raise ValueError(name)
        super()._start_span(name, attributes, result)


def test_a_failing_start_hook_keeps_the_stack_paired() -> None:
    spans = InMemorySpanExporter()
    listener = _FailingOnGreet(
        span_exporter=spans, log_exporter=InMemoryLogRecordExporter()
    )
    test, greet, log = (
        Result(name="T"),
        Result(name="Greet", full_name="Greet"),
        Result(name="Log"),
    )

    listener.start_test(None, test)
    with pytest.raises(ValueError, match="Greet"):
        listener.start_keyword(None, greet)
    listener.start_keyword(None, log)
    listener.end_keyword(None, log)
    listener.end_keyword(None, greet)
    listener.end_test(None, test)

    listener.close()
    by_name = {span.name: span for span in spans.get_finished_spans()}
    assert set(by_name) == {"T", "Log"}
    assert by_name["Log"].parent is not None
    assert by_name["Log"].parent.span_id == by_name["T"].context.span_id
    assert by_name["T"].end_time == END_NS


@pytest.mark.parametrize(
    ("args", "options", "expected"),
    [
        pytest.param(("500", "200"), {}, "500    200", id="strings"),
        pytest.param(({"a": 1}, 42), {}, "{'a': 1}    42", id="objects"),
        pytest.param((), {}, "", id="none"),
        pytest.param(("x",), {"export_keyword_arguments": False}, None, id="off"),
    ],
)
def test_keyword_arguments(
    args: Sequence[object], options: ListenerOptions, expected: str | None
) -> None:
    listener, spans, logs = _listener(**options)
    keyword = Result(args=args)

    listener.start_keyword(None, keyword)
    listener.end_keyword(None, keyword)

    (span,), _items = _finished(listener, spans, logs)
    assert attributes(span.attributes).get("rf.keyword.args") == expected


@pytest.mark.parametrize(
    ("level", "severity"),
    [
        pytest.param("TRACE", 1, id="trace"),
        pytest.param("DEBUG", 5, id="debug"),
        pytest.param("INFO", 9, id="info"),
        pytest.param("WARN", 13, id="warn"),
        pytest.param("ERROR", 17, id="error"),
        pytest.param("FAIL", 17, id="fail"),
        pytest.param("SKIP", 9, id="skip"),
    ],
)
def test_log_messages_become_records_of_the_open_span(
    level: str, severity: int
) -> None:
    listener, spans, logs = _listener()
    result = Result()

    listener.start_keyword(None, result)
    listener.log_message(Message("<b>x</b>", level=level, html=True))
    listener.end_keyword(None, result)

    (span,), (item,) = _finished(listener, spans, logs)
    record = item.log_record
    assert record.body == "<b>x</b>"
    assert record.severity_number is not None
    assert (record.severity_text, record.severity_number.value) == (level, severity)
    assert dict(attributes(record.attributes)) == {
        "rf.log.level": level,
        "rf.log.html": "true",
    }
    assert (record.trace_id, record.span_id) == (
        span.context.trace_id,
        span.context.span_id,
    )
    assert record.timestamp == START_NS


def test_a_log_message_outside_any_span_is_dropped() -> None:
    listener, spans, logs = _listener()

    listener.log_message(Message("orphan"))

    assert not _finished(listener, spans, logs)[1]


def test_log_bodies_are_cut_at_max_log_length() -> None:
    listener, spans, logs = _listener(max_log_length=5)

    listener.start_suite(None, Result())
    listener.log_message(Message("Hello world"))

    assert _records(_finished(listener, spans, logs)[1]) == [
        ("Hello", TEXT_LOG_ATTRIBUTES)
    ]


@pytest.mark.parametrize(
    ("message", "records"),
    [
        pytest.param(SELENIUM_SCREENSHOT, [(PNG, PNG_LOG_ATTRIBUTES)], id="selenium"),
        pytest.param(BROWSER_SCREENSHOT, [(PNG, PNG_LOG_ATTRIBUTES)], id="browser"),
        pytest.param(
            FILE_SCREENSHOT,
            [(FILE_SCREENSHOT, {"rf.log.level": "INFO", "rf.log.html": "true"})],
            id="file based stays html",
        ),
        pytest.param(
            f'Before <img src="data:image/jpeg;base64,{PNG}"> after',
            [(PNG, {**PNG_LOG_ATTRIBUTES, "rf.log.image": "image/jpeg"})],
            id="text around the image is dropped",
        ),
        pytest.param(
            f'<img src="data:image/png;base64,{PNG}"><img src="data:image/png;base64,{PNG}">',
            [(PNG, PNG_LOG_ATTRIBUTES), (PNG, PNG_LOG_ATTRIBUTES)],
            id="two images",
        ),
    ],
)
def test_embedded_screenshots_become_image_records(
    message: str, records: list[tuple[str, dict[str, str]]]
) -> None:
    listener, spans, logs = _listener()

    listener.start_keyword(None, Result())
    listener.log_message(Message(message, html=True))

    assert _records(_finished(listener, spans, logs)[1]) == records


def test_a_plain_text_message_is_never_an_image() -> None:
    listener, spans, logs = _listener()

    listener.start_keyword(None, Result())
    listener.log_message(Message(SELENIUM_SCREENSHOT, html=False))

    assert _records(_finished(listener, spans, logs)[1]) == [
        (SELENIUM_SCREENSHOT[:500], TEXT_LOG_ATTRIBUTES)
    ]


@pytest.mark.parametrize(
    ("options", "count", "bodies"),
    [
        pytest.param(
            {"max_image_size": 10},
            1,
            [f"Screenshot of {len(PNG)} bytes dropped (max_image_size 10)"],
            id="per image",
        ),
        pytest.param(
            {"max_image_size_total": len(PNG)},
            2,
            [
                PNG,
                f"Screenshot of {len(PNG)} bytes dropped (max_image_size_total {len(PNG)})",
            ],
            id="per run",
        ),
    ],
)
def test_screenshots_above_the_caps_are_dropped(
    options: ListenerOptions, count: int, bodies: list[str]
) -> None:
    listener, spans, logs = _listener(**options)

    listener.start_keyword(None, Result())
    for _ in range(count):
        listener.log_message(Message(SELENIUM_SCREENSHOT, html=True))

    assert _records(_finished(listener, spans, logs)[1]) == [
        (body, PNG_LOG_ATTRIBUTES if body == PNG else TEXT_LOG_ATTRIBUTES)
        for body in bodies
    ]


def test_resource_attributes_come_from_the_checkmk_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CHECKMK_RESOURCE_ATTRIBUTES",
        "cmk.application=otel_demo,cmk.suite=otel_demo_http,cmk.variant=,cmk.attempt=1",
    )
    monkeypatch.setenv("OTEL_SERVICE_NAME", "other")
    listener, spans, logs = _listener()

    listener.start_suite(None, Result())
    listener.log_message(Message("hello"))
    listener.end_suite(None, Result())

    expected = {
        "service.name": "robotmk",
        "cmk.application": "otel_demo",
        "cmk.suite": "otel_demo_http",
        "cmk.variant": "",
        "cmk.attempt": "1",
    }
    (span,), (item,) = _finished(listener, spans, logs)
    for resource in (span.resource.attributes, item.resource.attributes):
        assert {key: resource[key] for key in expected} == expected


def test_user_resource_attributes_are_merged_below_the_checkmk_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHECKMK_RESOURCE_ATTRIBUTES", "cmk.application=otel_demo")
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "team=payments,cmk.application=spoofed,service.name=x",
    )
    listener, spans, logs = _listener()

    listener.start_suite(None, Result())
    listener.end_suite(None, Result())

    (span,), _items = _finished(listener, spans, logs)
    resource = dict(span.resource.attributes)
    assert resource["team"] == "payments"
    assert resource["cmk.application"] == "otel_demo"
    assert resource["service.name"] == "robotmk"


@pytest.mark.parametrize(
    ("register_foreign_provider", "error", "message"),
    [
        pytest.param(
            lambda: trace.set_tracer_provider(TracerProvider()),
            GlobalTracerProviderAlreadySetError,
            r"tracer provider is already opentelemetry\.sdk\.trace\.TracerProvider",
            id="tracer",
        ),
        pytest.param(
            lambda: set_logger_provider(LoggerProvider()),
            GlobalLoggerProviderAlreadySetError,
            r"logger provider is already opentelemetry\.sdk\._logs\._internal\.LoggerProvider",
            id="logger",
        ),
    ],
)
def test_a_foreign_global_provider_is_refused_loudly(
    register_foreign_provider: Callable[[], None],
    error: type[RuntimeError],
    message: str,
) -> None:
    register_foreign_provider()

    with pytest.raises(error, match=message):
        _listener()


def test_close_without_a_run_is_harmless() -> None:
    listener, _spans, _logs = _listener()

    listener.close()
