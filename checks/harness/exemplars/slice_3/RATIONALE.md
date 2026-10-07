# Slice 3: Secrets and redaction

This historical regression example exercises the following mechanism: An injected secret resolver, missing-secret refusal and redaction of a synthetic token from persisted output.

Read [exemplar.py](exemplar.py) for the exact assertions and injected dependencies.
The implementation belongs to `gideon.automation.workflows`, including
`bindings.py` and `journal.py`. Run it with isolated state; consult the
[parent guide](../README.md) for invocation.

An injected model or worker response is component-test evidence, not a real
provider result. The presence of this example and its rationale does not claim
a fresh passing run, complete workflow coverage or production qualification.
