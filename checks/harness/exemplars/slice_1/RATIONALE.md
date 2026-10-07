# Slice 1: Controller and journal

This historical regression example exercises the following mechanism: A dependency-ordered sequence with an injected model response, terminal state and journal events.

Read [exemplar.py](exemplar.py) for the exact assertions and injected dependencies.
The implementation belongs to `gideon.automation.workflows`, including
`controller.py` and `journal.py`. Run it with isolated state; consult the
[parent guide](../README.md) for invocation.

An injected model or worker response is component-test evidence, not a real
provider result. The presence of this example and its rationale does not claim
a fresh passing run, complete workflow coverage or production qualification.
