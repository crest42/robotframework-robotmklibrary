*** Settings ***
Documentation    A keyword of another instrumentation inside a run.
Library    ${CURDIR}/current_span_library.py

*** Test Cases ***
Uses Another Instrumentation
    Open A Span
