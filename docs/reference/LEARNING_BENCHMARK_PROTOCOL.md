# Learning benchmark protocol

The skill-impact benchmark compares the same task with a skill surfaced and suppressed. It measures the effect of that skill under a recorded configuration; it does not establish general learning quality or production reliability.

The [runner](../../tooling/scripts/learning_benchmark.py), [task register and report store](../../runtime/gideon/assurance/evals/learning_bench.py), [learning verdicts](../../checks/harness/learning_verdict.py) and [comparison thresholds](../../checks/harness/fanout_measure.py) define the executable methodology.

## 1. Question

Does surfacing a named skill improve the task's verified outcome under comparable token spend? Report negative, inconclusive, withheld and unmeasured results alongside directional results.

## 2. Task register

### 2.1 Scope

The register contains ten scenarios. Each scenario supplies its fixture home and verifier. A skill name alone does not establish that the scenario can run or that its verifier measures every aspect of the skill.

### 2.2 Register

| Task ID | Skill | Scenario focus |
| --- | --- | --- |
| `sk_check_work` | `check-work` | Enumerated verification outcomes |
| `sk_task_project` | `task-and-project` | Task titles and status |
| `sk_knowledge_grounding` | `knowledge-grounding` | Grounded facts without distractors |
| `sk_memory_discipline` | `memory-discipline` | Persisted decisions without excessive capture |
| `sk_artifacts` | `artifacts` | Artifact references |
| `sk_editorial_document` | `editorial-document` | Requested section order |
| `sk_delegation` | `delegation` | Both parts of an enumerated split |
| `sk_grill` | `grill` | Questions rather than bare acceptance |
| `sk_best_of_n` | `best-of-n` | Three candidates and a selection criterion |
| `sk_visual_output` | `visual-output` | Widget envelope |

### 2.3 Version and fingerprint

The native register declares `TASK_SET_VERSION = 2`, `REPORT_SCHEMA = 2` and `PROVENANCE_SCHEMA = 2`. Reports retain the task-set version and scenario fingerprints. Changing a scenario must remain detectable through its fingerprint; a version label alone is insufficient evidence of identical tasks.

## 3. Arms and execution

The paired arms are `skills_on` and `skills_off`, corresponding to surfaced and suppressed skill bodies. Preflight must establish that suppression actually removes the skill body. Matrix cells run in isolated temporary homes seeded from the scenario's declared fixture home.

`--trials` selects trials per arm. Without a positive override, the runner reads `EvalsConfig.study_default_k` and falls back to the minimum trial floor if configuration cannot be read. Record the actual trial counts, including unequal surviving arms.

The script's `--preflight` and `--dry-run` modes do not call a model. `--run` executes cells; an explicit `--bind-provider Provider:model` supplies a provider binding. Unbound scripted-fixture execution does not establish live model performance. Use the matrix benchmark runner for this protocol rather than substituting another evaluation command with different isolation semantics.

## 4. Metrics

Retain verified task scores, trial counts, tool-call counts and spend provenance. The score difference is expressed in percentage points. Token comparison uses mean spend per scored trial, so missing trials are not represented as a reduction in total spend.

Distinguish observed spend, estimated spend and provider-reported token usage. Unrecorded usage is not zero usage. Reports expose `tokens_recorded` and `unrecorded_spend_cells` at task and run level. A run with no measured tasks cannot support a spend or skill-impact conclusion.

## 5. Verdict thresholds

The native comparison uses these thresholds:

- At least **3 scored trials per arm** are required.
- The ratio of mean token spend per trial must be within **5%** of parity.
- An absolute score difference **below 5 percentage points** is inconclusive.
- A within-arm score spread **at least 5 percentage points** is also inconclusive.

A directional verdict requires the remaining comparison conditions to pass. Missing token usage prevents a token-match claim. Preserve the emitted reason and verdict class rather than translating withheld verdicts into wins or losses.

## 6. Exclusions and missing observations

Absent verifier cells and cells without a score are counted as absent; they do not count as skills-off wins. Missing arms or wholly unmeasured tasks have no task verdict. Unequal surviving trial counts must remain visible: a per-trial token ratio does not make an unequal score comparison paired.

Skipped tasks and preflight blockers belong in the report. They must not silently disappear from the denominator or become numerical zeroes.

## 7. Preflight

Preflight checks scenario availability, fixture-home resolution, skill-body availability and suppression. It returns blockers without calling a model. Passing preflight establishes run eligibility, not a measured outcome.

```sh
python tooling/scripts/learning_benchmark.py --preflight
python tooling/scripts/learning_benchmark.py --dry-run
python tooling/scripts/learning_benchmark.py --run --bind-provider Provider:model --trials 5
```

## 8. Publication and reproduction

Retain the report, declared schemas, scenario fingerprints, prompt-pack fingerprint, configuration snapshot reference, provider/model binding, cell artifacts, measured and absent counts, skipped tasks, verdict reasons and spend provenance. A fixture result must be identified as such. Publication must preserve inconclusive and unmeasured outcomes.

### Reproduction (V4)

The native reproduction predicate requires matching task-set versions, matching nonempty scenario fingerprints, matching nonempty prompt-pack fingerprints and configuration snapshot references, and the same verdict class for each task with at least one measured verdict. A missing verdict is a change, not an agreement. Schema notes report absent or older metadata; they do not manufacture missing evidence.

```sh
python tooling/scripts/learning_benchmark.py --check-reproduction --reproduce BASELINE_RUN_ID --against CURRENT_RUN_ID
```

Report the predicate's individual checks and changed tasks. Agreement under this predicate is narrower than identical transcripts, identical spend or production qualification.
