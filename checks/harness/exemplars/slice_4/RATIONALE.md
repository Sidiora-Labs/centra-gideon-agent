# Slice 4: Binding closure and rerun

This historical regression example exercises the following mechanism: A binding-dependent node edit, its affected closure and controller resume-cache behavior.

Read [exemplar.py](exemplar.py) for the exact assertions and injected dependencies.
The implementation belongs to `gideon.automation.workflows`, including
`mutations.py` and `controller.py`. Run it with isolated state; consult the
[parent guide](../README.md) for invocation.

An injected model or worker response is component-test evidence, not a real
provider result. The presence of this example and its rationale does not claim
a fresh passing run, complete workflow coverage or production qualification.
