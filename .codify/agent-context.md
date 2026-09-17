# gideon-agent-harness — Agent Context

<!-- codify-owned: graph-agent-context v1 -->

_Generated graph context owned by `cg agentmd`. Regenerate with `cg agentmd --write` after significant changes. Workflow instructions remain owned by `cg spec render`._

## Languages

| Language | Files | Lines |
|---|---:|---:|
| python | 2605 | 929694 |
| typescript | 1458 | 178601 |
| javascript | 57 | 9407 |

4120 source files, 1117702 lines total.

## Directory map

- `apps/` — 1510 files, 186321 lines (mostly typescript)
- `assets/` — 1 files, 88 lines (mostly python)
- `checks/` — 1386 files, 512137 lines (mostly python)
- `examples/` — 7 files, 1174 lines (mostly python)
- `packages/` — 7 files, 597 lines (mostly python)
- `runtime/` — 1181 files, 409501 lines (mostly python)
- `(root)` — 1 files, 25 lines (mostly python)
- `tooling/` — 27 files, 7859 lines (mostly python)

## Build & tooling

- `Makefile` — make
- `package.json` — npm/node (scripts: `dev` `build` `typecheck:web` `test:web` `audit:consistency` `test:desktop` `test:mobile` `smoke:render` `validate:apps`)
- `pyproject.toml` — Python (pyproject)

## Entry points

- function `main` — `apps/mobile/scripts/generate_store_assets.py:65`
- function `main` — `assets/identity/check_assets.py:26`
- function `main` — `checks/harness/cli.py:669`
- function `main` — `checks/harness/exemplars/__main__.py:21`
- function `main` — `checks/harness/exemplars/slice_0/exemplar.py:59`
- function `main` — `checks/harness/exemplars/slice_1/exemplar.py:68`
- function `main` — `checks/harness/exemplars/slice_2/exemplar.py:74`
- function `main` — `checks/harness/exemplars/slice_3/exemplar.py:81`
- function `main` — `checks/harness/exemplars/slice_4/exemplar.py:128`
- function `main` — `checks/harness/exemplars/slice_5/exemplar.py:66`
- function `main` — `checks/harness/runtime_lifecycle_probe.py:15`
- function `main` — `examples/registry/validate_registry.py:970`
- function `main` — `runtime/gideon/assurance/evals/child.py:313`
- function `main` — `runtime/gideon/assurance/evals/optimize.py:1095`
- function `main` — `runtime/gideon/assurance/evals/voice_engine_bakeoff.py:512`

## HTTP routes

| Method | Pattern | Handler | Where |
|---|---|---|---|
| * | `/Users/me/notes/today.md` | — | `apps/console/src/features/settings/scratchpadPath.test.tsx:30` |
| * | `/a` | — | `apps/console/src/features/chat/toolRenderers/nativeInput.test.tsx:23` |
| * | `/a` | — | `tooling/scripts/lib/app_validate_report.test.mjs:156` |
| * | `/a` | — | `tooling/scripts/lib/app_validate_report.test.mjs:169` |
| * | `/a` | — | `tooling/scripts/lib/app_validate_report.test.mjs:181` |
| * | `/a/b.py` | — | `apps/console/src/features/chat/toolRenderers/nativeInput.test.tsx:20` |
| * | `/a/b.py` | — | `apps/console/src/features/chat/toolRenderers/nativeInput.test.tsx:21` |
| * | `/a/b.py` | — | `apps/console/src/features/chat/toolRenderers/nativeInput.test.tsx:34` |
| * | `/b` | — | `tooling/scripts/lib/app_validate_report.test.mjs:157` |
| * | `/b` | — | `tooling/scripts/lib/app_validate_report.test.mjs:174` |
| * | `/home/projects/p-1/context/decisions.md` | — | `apps/console/src/features/settings/alwaysOnViewer.test.tsx:38` |
| * | `/home/projects/p-1/context/overview.md` | — | `apps/console/src/features/settings/alwaysOnViewer.test.tsx:32` |
| * | `/home/skills/house-conventions/SKILL.md` | — | `apps/console/src/features/settings/alwaysOnViewer.test.tsx:26` |
| * | `/home/u/.gideon/security_events.20260902T010203Z.bak.jsonl` | — | `apps/console/src/features/settings/auditRotateLabel.test.tsx:61` |
| * | `/notes` | — | `tooling/scripts/lib/app_validate_report.test.mjs:196` |
| * | `/tmp/home/workflows/runs/r1` | — | `apps/console/src/features/workflows/DeliverablePanel.test.tsx:45` |
| * | `/tmp/worktrees/run-1` | — | `apps/console/src/features/workflows/WorkspacePanel.test.tsx:31` |
| * | `/tmp/worktrees/run-1` | — | `apps/console/src/features/workflows/WorkspacePanel.test.tsx:39` |
| * | `/w/x.md` | — | `apps/console/src/features/artifacts/artifactReadFamily.test.tsx:7` |
| * | `/x` | — | `apps/console/src/features/skills/learningSummaryBlock.test.tsx:17` |
| * | `/x` | — | `tooling/scripts/lib/app_validate_report.test.mjs:44` |
| * | `/x` | — | `tooling/scripts/lib/app_validate_report.test.mjs:137` |
| * | `/x` | — | `tooling/scripts/lib/app_validate_report.test.mjs:145` |

## Load-bearing symbols (most referenced)

- `get` (method, 14983 refs) — `apps/console/src/features/triggers/createFormDeadEnd.test.tsx:20`
- `expect` (function, 14679 refs) — `runtime/gideon/integrations/acp/reader.py:50`
- `str` (function, 9342 refs) — `apps/console/src/features/chat/toolRenderers/native.tsx:15`
- `append` (function, 5799 refs) — `apps/console/src/features/loops/LoopPlanReview.tsx:540`
- `strip` (function, 3501 refs) — `apps/console/src/app/shell/clipboardReportsFailure.test.tsx:83`
- `one` (function, 3401 refs) — `checks/runtime/test_ci_concurrency_dedupe.py:67`
- `set` (function, 2990 refs) — `apps/console/src/features/apps/appConfigForm.tsx:161`
- `run` (function, 2911 refs) — `apps/console/src/app/shell/CommandPalette.tsx:22`
- `map` (function, 2383 refs) — `apps/console/src/app/shell/appSdkUi.test.tsx:244`
- `list` (function, 2354 refs) — `apps/console/src/features/tools/ToolsPage.tsx:171`
- `render` (method, 2192 refs) — `apps/console/src/app/shell/ErrorBoundary.tsx:42`
- `test` (function, 2043 refs) — `apps/console/src/features/triggers/LifecycleDetail.tsx:59`
- `field` (function, 1985 refs) — `apps/console/src/features/knowledge/readerFindBar.test.tsx:39`
- `loads` (function, 1889 refs) — `runtime/gideon/operations/durability/registry.py:111`
- `describe` (method, 1856 refs) — `apps/desktop/src/native/login-item.js:17`

## Querying this codebase

This project is indexed by Codify (SQLite + FTS5, 100% local). Prefer these over grep/file-walking — one call returns definitions, snippets, and call edges:

```bash
cg context <query>      # symbols + snippets + callers/callees + routes
cg search <text>        # instant name/full-text search
cg symbol <name>        # definition + snippet + reference count
cg impact <name> -d 3   # who breaks if this changes
cg routes [filter]      # URL pattern -> handler
cg changes              # impact radius of uncommitted edits
```

All of the above accept `--json`. The graph auto-syncs via `cg watch`, or connect over MCP with `cg mcp-install`.
