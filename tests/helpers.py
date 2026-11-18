"""Types and accessors shared by the unit and integration tests."""

from collections.abc import Mapping
from typing import TypedDict

from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.util.types import AttributeValue


class ListenerOptions(TypedDict, total=False):
    """The tunable arguments of RobotmkListener, as the test helpers forward them."""

    export_control_structures: bool
    export_keyword_arguments: bool
    max_log_length: int
    max_image_size: int
    max_image_size_total: int


def attributes(
    value: Mapping[str, AttributeValue] | None,
) -> Mapping[str, AttributeValue]:
    """The attributes of a span or record, which the SDK types as optional."""
    assert value is not None
    return value


def scope(value: InstrumentationScope | None) -> InstrumentationScope:
    """The instrumentation scope of a span or record, which the SDK types as optional."""
    assert value is not None
    return value
