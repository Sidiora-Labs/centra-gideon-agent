# Slice 0: Validation and bindings

This historical regression example exercises the following mechanism: A valid graph, a dangling node reference, and whole-value binding type preservation.

Read [exemplar.py](exemplar.py) for the exact assertions and injected dependencies.
The implementation belongs to `gideon.automation.workflows`, including
`validator.py` and `bindings.py`. Run it with isolated state; consult the
[parent guide](../README.md) for invocation.

An injected model or worker response is component-test evidence, not a real
provider result. The presence of this example and its rationale does not claim
a fresh passing run, complete workflow coverage or production qualification.
