"""The listener inside real Robot Framework runs."""

import io
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Unpack

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from robot import run as run_robot

from RobotmkLibrary.instrumentation.listener import RobotmkListener
from tests.helpers import ListenerOptions, attributes, scope

SUITES = Path(__file__).parent / "suites"
HTML_LOG_ATTRIBUTES = {"rf.log.level": "INFO", "rf.log.html": "true"}
TEXT_LOG_ATTRIBUTES = {"rf.log.level": "INFO", "rf.log.html": "false"}


class Export:
    """A listener with in-memory exporters and the robot run that feeds it."""

    def __init__(self, **options: Unpack[ListenerOptions]) -> None:
        self.spans = InMemorySpanExporter()
        self.logs = InMemoryLogRecordExporter()
        self.listener = RobotmkListener(
            span_exporter=self.spans, log_exporter=self.logs, **options
        )

    def run(self, target: str, *listeners: object, **robot_options: object) -> int:
        return int(
            run_robot(
                str(SUITES / target),
                listener=[*listeners, self.listener],
                output=None,
                log=None,
                report=None,
                stdout=io.StringIO(),
                **robot_options,
            )
        )

    def finished_spans(self) -> Sequence[ReadableSpan]:
        return self.spans.get_finished_spans()

    def spans_by_name(self) -> dict[str, ReadableSpan]:
        return {span.name: span for span in self.finished_spans()}

    def records(self) -> list[tuple[str, object, dict[str, object]]]:
        """Every log record as (span name, body, attributes), in export order."""
        spans = {span.context.span_id: span for span in self.finished_spans()}
        return [
            (
                spans[item.log_record.span_id].name,
                item.log_record.body,
                dict(attributes(item.log_record.attributes)),
            )
            for item in self.logs.get_finished_logs()
        ]


class RobotTimes:
    """Listener that records the Robot Framework times of suites, tests and keywords."""

    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self) -> None:
        self.times: dict[str, tuple[datetime, datetime]] = {}

    def end_suite(self, _data: object, result: object) -> None:
        self._record(result, "name")

    def end_test(self, _data: object, result: object) -> None:
        self._record(result, "name")

    def end_keyword(self, _data: object, result: object) -> None:
        self._record(result, "full_name")

    def _record(self, result: object, name_attribute: str) -> None:
        name = str(getattr(result, name_attribute))
        self.times[name] = (
            getattr(result, "start_time"),  # noqa: B009
            getattr(result, "end_time"),  # noqa: B009
        )


def _nanoseconds(moment: datetime) -> int:
    return int(moment.timestamp()) * 1_000_000_000 + moment.microsecond * 1_000


def _spans(
    spans: Sequence[ReadableSpan],
) -> list[tuple[str, str | None, str, str, str]]:
    """(name, parent name, rf.type, rf.status, status code) per span, parents first."""
    names = {span.context.span_id: span.name for span in spans}
    return [
        (
            span.name,
            None if span.parent is None else names[span.parent.span_id],
            str(attributes(span.attributes)["rf.type"]),
            str(attributes(span.attributes)["rf.status"]),
            span.status.status_code.name,
        )
        for span in sorted(
            spans, key=lambda span: (span.start_time or 0, -(span.end_time or 0))
        )
    ]


def test_one_trace_of_nested_spans() -> None:
    export = Export()

    assert export.run("demo.robot") == 1

    spans = export.finished_spans()
    assert _spans(spans) == [
        ("Demo", None, "suite", "FAIL", "ERROR"),
        ("Passes", "Demo", "test", "PASS", "OK"),
        ("Greet", "Passes", "keyword", "PASS", "OK"),
        ("BuiltIn.Log", "Greet", "keyword", "PASS", "OK"),
        ("BuiltIn.Log", "Greet", "keyword", "PASS", "OK"),
        ("BuiltIn.Log", "Passes", "keyword", "PASS", "OK"),
        ("Fails", "Demo", "test", "FAIL", "ERROR"),
        ("Greet", "Fails", "keyword", "PASS", "OK"),
        ("BuiltIn.Log", "Greet", "keyword", "PASS", "OK"),
        ("BuiltIn.Log", "Greet", "keyword", "PASS", "OK"),
        ("BuiltIn.Should Be Equal", "Fails", "keyword", "FAIL", "ERROR"),
        ("Skips", "Demo", "test", "SKIP", "UNSET"),
        ("BuiltIn.Skip", "Skips", "keyword", "SKIP", "UNSET"),
    ]
    assert len({span.context.trace_id for span in spans}) == 1


def test_setup_and_teardown_keywords_carry_their_type() -> None:
    export = Export()
    export.run("control.robot")

    spans = export.spans_by_name()
    by_type = {
        attributes(span.attributes)["rf.keyword.type"]: span
        for span in export.finished_spans()
        if attributes(span.attributes)["rf.type"] == "keyword"
        and attributes(span.attributes)["rf.keyword.type"] != "KEYWORD"
    }
    assert set(by_type) == {"SETUP", "TEARDOWN"}
    assert by_type["SETUP"].parent is not None
    assert by_type["SETUP"].parent.span_id == spans["Control"].context.span_id
    assert by_type["TEARDOWN"].parent is not None
    assert (
        by_type["TEARDOWN"].parent.span_id
        == spans["Control Structures"].context.span_id
    )


@pytest.mark.parametrize(
    "name", ["Demo", "Passes", "Fails", "BuiltIn.Should Be Equal", "BuiltIn.Skip"]
)
def test_span_times_are_the_robot_framework_times(name: str) -> None:
    times = RobotTimes()
    export = Export()
    export.run("demo.robot", times)

    span = export.spans_by_name()[name]
    start, end = times.times[name]
    assert (span.start_time, span.end_time) == (_nanoseconds(start), _nanoseconds(end))


def test_the_name_override_is_the_root_suite_name() -> None:
    export = Export()
    export.run("demo.robot", name="Custom Root")

    spans = export.spans_by_name()
    assert spans["Custom Root"].parent is None
    assert (
        attributes(spans["Passes"].attributes)["rf.test.full_name"]
        == "Custom Root.Passes"
    )


def test_a_directory_target_nests_the_suites() -> None:
    export = Export()
    export.run("", test="Passes")

    spans = export.spans_by_name()
    assert spans["Suites"].parent is None
    assert spans["Demo"].parent is not None
    assert spans["Demo"].parent.span_id == spans["Suites"].context.span_id
    assert (
        attributes(spans["Passes"].attributes)["rf.test.full_name"]
        == "Suites.Demo.Passes"
    )


def test_log_records_belong_to_the_innermost_span() -> None:
    export = Export()
    export.run("demo.robot")

    assert export.records() == [
        ("BuiltIn.Log", "Hello world", TEXT_LOG_ATTRIBUTES),
        ("BuiltIn.Log", "<b>bold</b>", HTML_LOG_ATTRIBUTES),
        ("BuiltIn.Log", "careful", {"rf.log.level": "WARN", "rf.log.html": "false"}),
        ("BuiltIn.Log", "Hello nobody", TEXT_LOG_ATTRIBUTES),
        ("BuiltIn.Log", "<b>bold</b>", HTML_LOG_ATTRIBUTES),
        (
            "BuiltIn.Should Be Equal",
            "500 != 200",
            {"rf.log.level": "FAIL", "rf.log.html": "false"},
        ),
        ("BuiltIn.Skip", "not today", {"rf.log.level": "SKIP", "rf.log.html": "false"}),
    ]


def test_log_record_timestamps_lie_within_their_span() -> None:
    export = Export()
    export.run("demo.robot")

    spans = {span.context.span_id: span for span in export.finished_spans()}
    records = [item.log_record for item in export.logs.get_finished_logs()]
    assert records
    for record in records:
        span = spans[record.span_id]
        assert span.start_time is not None
        assert span.end_time is not None
        assert record.timestamp is not None
        assert span.start_time <= record.timestamp <= span.end_time


def test_control_structures_become_spans_on_request() -> None:
    export = Export(export_control_structures=True)
    export.run("control.robot")

    assert _spans(export.finished_spans()) == [
        ("Control", None, "suite", "PASS", "OK"),
        ("BuiltIn.Log", "Control", "keyword", "PASS", "OK"),
        ("Control Structures", "Control", "test", "PASS", "OK"),
        ("FOR", "Control Structures", "control", "PASS", "OK"),
        ("ITERATION", "FOR", "control", "PASS", "OK"),
        ("BuiltIn.Log", "ITERATION", "keyword", "PASS", "OK"),
        ("ITERATION", "FOR", "control", "PASS", "OK"),
        ("BuiltIn.Log", "ITERATION", "keyword", "PASS", "OK"),
        ("IF/ELSE ROOT", "Control Structures", "control", "PASS", "OK"),
        ("IF", "IF/ELSE ROOT", "control", "PASS", "OK"),
        ("BuiltIn.Log", "IF", "keyword", "PASS", "OK"),
        ("ELSE", "IF/ELSE ROOT", "control", "NOT RUN", "UNSET"),
        ("BuiltIn.Log", "ELSE", "keyword", "NOT RUN", "UNSET"),
        ("TRY/EXCEPT ROOT", "Control Structures", "control", "PASS", "OK"),
        ("TRY", "TRY/EXCEPT ROOT", "control", "FAIL", "ERROR"),
        ("BuiltIn.Should Be Equal", "TRY", "keyword", "FAIL", "ERROR"),
        ("EXCEPT", "TRY/EXCEPT ROOT", "control", "PASS", "OK"),
        ("BuiltIn.Log", "EXCEPT", "keyword", "PASS", "OK"),
        ("WHILE", "Control Structures", "control", "NOT RUN", "UNSET"),
        ("ITERATION", "WHILE", "control", "NOT RUN", "UNSET"),
        ("BuiltIn.Log", "ITERATION", "keyword", "NOT RUN", "UNSET"),
        ("VAR", "Control Structures", "control", "PASS", "OK"),
        ("BuiltIn.Evaluate", "Control Structures", "keyword", "PASS", "OK"),
        ("BuiltIn.Log", "Control Structures", "keyword", "PASS", "OK"),
    ]


def test_other_instrumentations_nest_below_the_running_keyword() -> None:
    export = Export()
    export.run("library.robot")

    spans = export.spans_by_name()
    other = spans["http.request"]
    assert other.parent is not None
    assert (
        other.parent.span_id
        == spans["current_span_library.Open A Span"].context.span_id
    )
    assert other.context.trace_id == spans["Library"].context.trace_id
    assert scope(other.instrumentation_scope).name == "other-instrumentation"


def test_an_unreachable_collector_does_not_fail_the_run() -> None:
    started = time.monotonic()

    return_code: int = run_robot(
        str(SUITES / "demo.robot"),
        test="Passes",
        listener="RobotmkLibrary.instrumentation.listener.RobotmkListener;endpoint=http://127.0.0.1:9;timeout=1",
        output=None,
        log=None,
        report=None,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert return_code == 0
    assert time.monotonic() - started < 15
