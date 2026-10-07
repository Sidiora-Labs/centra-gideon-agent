# Workflow exemplars

Each `slice_*/` contains an executable example, a smoke script and a rationale.
These are historical regression examples for selected workflow mechanisms, not
a complete product-qualification suite. Several examples inject model or worker
responses; their results do not establish live-provider behavior.

| Directory | Mechanism exercised |
| --- | --- |
| `slice_0/` | Structural validation and binding type preservation |
| `slice_1/` | Dependency-ordered sequence, controller and journal |
| `slice_2/` | Missing required artifact refusal |
| `slice_3/` | Secret resolution and journal redaction |
| `slice_4/` | Binding closure and targeted rerun |
| `slice_5/` | Needs-input and unanswered gate timeout |

An example can be invoked from the repository root with its actual module path:

```sh
.venv/bin/python -m checks.harness.exemplars.slice_0.exemplar
```

Use isolated `GIDEON_HOME` state for execution. Discovery and completeness checks
are implemented in [__init__.py](__init__.py); inspect the profile's invocation
when running the aggregate harness. A module's own exit status describes only
its asserted mechanism. This documentation refresh did not execute the examples.

Recorded replay scenarios live under [../traces/](../traces). Replay compares
event projections; it is distinct from creating a fresh runtime execution.
