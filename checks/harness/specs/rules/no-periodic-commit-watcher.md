---
id: no-periodic-commit-watcher
type: ai-coding-rule
statement: >
  The Self-QA commit watch is driven by the vcs (file-watch) trigger and by nothing else —
  no module that names the commit watch may also state a periodic cadence (a trigger kind of
  interval/cron/schedule, or a schedule/interval_*/cron/every field).
appliesTo:
  - runtime/gideon/assurance/selfqa/watch.py
  - runtime/gideon/assurance/selfqa/install.py
  - runtime/gideon/integrations/action_providers/selfqa_watch_provider.py
scanner: no-periodic-commit-watcher
source: >
  The Wave-2 companion shipped the commit delta as a sandboxed cron script in
  ~/.gideon/crons/ on an interval trigger, because no vcs trigger existed yet. It does now
  (gideon.automation.triggers.file_watch.vcs_patterns watches .git/refs/heads/* and
  .git/HEAD), so install.reconcile converges a kind="file" trigger and deletes the retired
  script's artifacts. A clock put back beside that trigger gives one job two owners: both
  fire on the same commits, the watcher's state advances under whichever wins, and the loser
  either re-runs the same SHAs or reports "no new commits" for work it never saw.
expiry_condition: >
  Retire if the vcs trigger stops being able to observe commits on its own (a repo the file
  watcher cannot reach, e.g. a bare remote), so a poll is the only honest mechanism left.
---

# The commit watcher is version-control-triggered, never periodic

`gideon.assurance.selfqa.watch` resolves what one fire means — the SHAs between the last
seen HEAD and the current one — and `selfqa-commit-watch` turns that verdict into a
workflow run. Neither of them decides *when* to look: that is the `vcs` file-watch preset,
armed by `install.reconcile` as a `kind="file"` trigger over `agent.self_qa.watched_repo`.

The interim seam was the opposite arrangement — a cron script materialized into the fenced
crons directory and run on an interval — and it is retired, artifacts and all.

## What compliance looks like

A module that names the commit watch states no cadence. It binds to the vcs preset:

```python
trigger.kind = "file"
trigger.spec = {"paths": vcs_patterns(repo), "dedup": "content"}
```

and never `kind="interval"`, `kind="cron"`, `schedule=…`, `interval_minutes=…` or a cron
expression. The watcher's own first-sight and state-advance rules assume exactly one
observer; a second, clock-driven one either double-fires a run or advances the state past
commits the real fire then reports as already seen.

The scanner check `no-periodic-commit-watcher` reads each `runtime/gideon/**.py` file's
literals: if a non-docstring string names the commit watch (`selfqa-commit-watch`,
`selfqa_commit_watch`, `commit_watch.`) and the same module writes a periodic trigger kind
or a scheduling field, it reports an ERROR at that line. Docstrings are excluded on
purpose — the retirement is narrated in these very modules, and prose about a removed cron
script is not a cron script.
