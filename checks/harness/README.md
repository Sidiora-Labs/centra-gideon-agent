# Development verification harness

`checks/harness` contains repository tooling for specification references, command
profiles, static boundary checks, recorded event replay, and measurements. It is outside
the packaged runtime source tree; runtime code must not import it.

These tools describe and exercise particular checks. They do not establish complete
product correctness, live vendor behavior, or release qualification by themselves.

## Main modules

| Module | Purpose |
|---|---|
| `specs.py`, `validate_refs.py` | Specification shape and referenced test/profile/check validation. |
| `profiles.py`, `selection.py` | Named command profiles and additional requirements selected from a diff. |
| `scanner.py` | Static architectural-boundary findings. |
| `diff.py` | Changed-file/line and commit introspection. |
| `replay.py`, `baselines.py`, `traces/` | Recorded event metrics and expected projections. |
| `fanout_measure.py` | Token-matched fan-out comparisons. |
| `worktree_bench.py` | Worktree hydration observations. |
| `cli.py` | Command parsing, reporting, and execution. |
| `specs/`, `exemplars/` | Repository specifications and executable examples. |

## Commands

Run from the repository root with the development Python environment:

```bash
python -m checks.harness validate
python -m checks.harness validate --fast
python -m checks.harness explain TASK_ID
python -m checks.harness run TASK_ID
python -m checks.harness run --diff
python -m checks.harness scan
python -m checks.harness replay
python -m checks.harness fanout-measure observations.json
```

Replace `TASK_ID` with an actual task specification. `validate --fast` checks shapes
without full referenced-test collection. `explain` shows selected requirements; `run`
executes them. Diff selection can add requirements to the declared task scope. Inspect
`profiles.py` for the exact commands and prerequisites before launching a profile;
command configuration can itself become stale.

The CLI also defines resume-audit and benchmark commands. Use `python -m checks.harness
--help` and the relevant subcommand help for current inputs. Profiles resolve the
checkout's Python environment where present, otherwise the running interpreter.

## Specifications and findings

Rule, scenario, and task specifications use frontmatter and stable references such as
pytest node IDs, path globs, profile names, and scanner check IDs. A resolved reference
shows that an anchor exists; it is not proof that the referenced test ran or passed.

Static errors and warnings have different semantics. Heuristic warnings need inspection;
absence of findings is not an exhaustive boundary audit. Keep the current declared
severity and command exit behavior visible rather than presenting every advisory as a
runtime denial.

## Replay and measurements

Trace recording is runtime-owned and redacted; folding and metric analysis are harness
consumers. A recorded trace can verify ordering, deduplication, expected folding, and
specific baseline comparisons. It does not replay real side effects or cover paths
absent from that recording.

Fan-out comparison requires observations from both arms, token-spend matching, and
sufficient trials. The tool can return an inconclusive or refused comparison as an honest
result. A zero exit for such a result is not a measured performance win. Inspect the
verdict and sample limitations, not just the process status.

Report the actual revision, commands, exits, prerequisites, and coverage exercised.
Preserve meaningful baseline and refusal assertions when updating the tooling.
