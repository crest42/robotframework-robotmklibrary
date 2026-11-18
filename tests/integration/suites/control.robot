*** Settings ***
Documentation    Control structures, setup and teardown for the listener tests.
Suite Setup    Log    suite setup
Test Teardown    Log    test teardown

*** Test Cases ***
Control Structures
    FOR    ${i}    IN RANGE    2
        Log    loop ${i}
    END
    IF    True
        Log    if branch
    ELSE
        Log    else branch
    END
    TRY
        Should Be Equal    1    2
    EXCEPT
        Log    caught
    END
    WHILE    False
        Log    never
    END
    VAR    ${v}    value
    ${r}=    Evaluate    1 + 1
