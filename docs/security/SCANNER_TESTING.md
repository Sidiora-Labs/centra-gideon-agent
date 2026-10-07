# Scanner tests and limitations

Gideon's supply-chain scanner performs static inspection of staged content. Tests exercise
specific rules and installation integrity checks; they do not establish that arbitrary
code is safe or contained. The implementation is
`runtime/gideon/security/supply_chain.py`, with commit-side checks in
`runtime/gideon/extensions/skills/marketplace.py` and app lifecycle checks in
`runtime/gideon/extensions/apps/app_manager.py`.

## Run the existing corpus

Use the repository's development environment, then run from the repository root:

```bash
python -m pytest -n 0 --no-cov checks/runtime/security/
```

Prepare that environment using [CONTRIBUTING.md](../../CONTRIBUTING.md); a source
installation also has native build prerequisites. This documentation refresh did not
run the corpus or certify its current pass status.

Corpus cases are JSON data under `checks/runtime/security/corpus/`. The harness in
`checks/runtime/security/test_scanner_adversarial.py` materializes staged fixtures and
routes their expected result to registered handlers. Completeness assertions detect
missing cases or unwired expectations. The full workflow declares a separate
`security-corpus` job; configuration alone is not evidence of its latest result.

## Coverage areas

| Area | What the existing tests target |
|---|---|
| Archives and paths | Traversal, absolute paths, collision cases, staging links, and confinement of committed content. |
| Integrity races | Fetch/scan/commit identity, concurrent changes, commit-side checks, and post-install integrity records. |
| Verdict evasion | Destructive script patterns, execution pipelines, trust-tier behavior, and terminal refusal. |
| Invisible characters | Bidi and zero-width text, their actual verdicts, and warning confirmation. |
| Degenerate manifests | Missing or malformed skill metadata and size-boundary behavior. |
| Baseline tampering | Packaged denylist digest/integrity, runtime reassertion, refusal, and audit evidence. |
| Reachability | Conditions under which an inert dangerous literal can be reclassified to a warning. |
| Native destruction | Python AST analysis of deletion, destructive walks, and truncation at protected target paths. |

Dedicated coverage is in `test_app_installs_what_was_scanned.py`,
`test_app_staging_links.py`, `test_mode_independence.py`, `test_scanner_reachability.py`,
`test_scanner_recall.py`, and `test_scanner_rule_languages.py` under
`checks/runtime/security/`. Consult their actual assertions instead of a fixed historical
count of passing cases or mutations.

## Verdicts and reachability

`dangerous` is a terminal refusal, including when confirmation is supplied. Warnings have
source-trust and consent behavior; a warning is not a refusal in every configuration.
A clean result means no configured rule produced a finding, not that execution is safe.

Reachability analysis can lower a matched dangerous literal to a warning only when its
static conditions establish that the bundle cannot execute it through the analyzed
paths. It checks literal placement, execution sinks, external references, dynamic graph
behavior, and top-level calls. It does not exempt a file merely because its name starts
with `test_`. Failure to establish the conditions retains the dangerous result.

Native destruction rules analyze Python call sites and resolvable protected targets,
including recognized aliases and limited bindings. They do not treat every legitimate
`rmtree` as dangerous. Unresolved runtime targets, other-language semantics, dynamic
execution, and dependency behavior remain limits of static analysis.

## Evidence and residual risk

A scanner verdict and byte identity answer different questions. A useful integrity test
compares the actually scanned bytes with the committed bytes, not just whether both
scans produced the same verdict. Verify update consumers and selected skill copies as
well as initial installation.

The scanner bounds file reads and uses language/surface rules. Binary data, oversized
content, obfuscation, runtime-computed targets, fetched dependencies, and unknown
execution paths may exceed a rule's reach. Commit-side checks can cover some staging
limits, but that does not justify an unbounded “every byte was semantically checked”
claim. Inspect the actual refusal and metadata for the tested case.

A live in-process attacker can alter Python objects or the installed package; static
installation checks are not an isolation boundary against that attacker. Post-install
integrity records detect certain changes but do not prevent execution by themselves.

Mutation tests deliberately weaken individual controls to check that an assertion detects
the regression. They are test evidence for those mutations, not proof that every possible
bypass is detected. Keep benign controls too, so refusing all content cannot masquerade
as correct enforcement.

## Add a case

Add inert JSON under the relevant corpus directory, including its ID, class, summary,
expected handler, and files or variants. Wire any new handler, exercise the actual
consumer, and preserve both refusal and benign behavior assertions. Use an isolated home
and temporary staging directories; never modify the installed user denylist or real
app data to demonstrate a fixture attack.

If a case reveals a gap, report the observed behavior and its limit. Do not weaken an
expectation solely to make the suite pass. See [review scope](REVIEW_SCOPE.md),
[signing](SIGNING.md), and [limitations](LIMITATIONS.md).
