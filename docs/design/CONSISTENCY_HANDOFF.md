# Design-system consistency: owner and CI handoff

**Plan:** DESIGN-SYSTEM-CONSISTENCY
**Status at handoff:** every autonomously safe S1 to S3 item is done and green on `main`. What is left changes pixels, and all of it is gated on one thing the loop could not do unattended: seeding the visual-regression harness with an authenticated session. This page is the minutes-long unblock.

---

## What is done (green on `main`, no owner action needed)

- **S1 audit.** `docs/design/CONSISTENCY_AUDIT.md` is the ranked drift map, and `apps/console/src/design/consistencyAudit.report.ts` is a live reporter you run with `npm run audit:consistency` to write `consistency-audit.json`. Key finding: the token layer is near-clean, and the real drift is **primitive adoption** (420 raw `<button>`, 206 raw form elements), concentrated in the two mega-pages. Accessibility is clean app-wide, through the global reduced-motion and `:focus-visible` rules.
- **Harness (infra).** `apps/console/e2e/` plus `apps/console/playwright.config.ts` (`toHaveScreenshot`, platform-qualified baselines, axe wcag2aa). It parses to 64 tests, chromium installs, and the deps are locked in `package-lock.json`.
- **Primitive and patterns.** `apps/console/src/ui/TextField.tsx` (`TextField`/`TextArea`, the audit's one genuine gap) plus the full `docs/design/PATTERNS.md` gallery: TextField, Button, Modal, the two empty states, confirm/prompt/alert, the skeleton family, ListRow, and fvs()/.fw-*.
- **CI ratchets, four live in the `web` vitest job.** token-lint-strict (`tokenLint.test.ts`), primitive-adoption plus inline-font-weight (`primitiveAdoption.test.ts`), and the inert-utility rail (`inertUtilities.test.ts` plus `inertUtilities.allowlist.json`, issue #556). That last rail catches a `text-*`, `bg-*` or `border-*` class that names a token which does not exist: it emits no CSS, so the style is silently absent, and Tailwind itself is the oracle. New drift turns CI red.
- **Zero-pixel conformance.** Notification unread-rail and tone-chip consolidation, about 22 inline font-weights moved to `fvs()`, and empty-state name disambiguation.

## The one blocker, and the unblock (about 5 minutes)

The built SPA gates its first render on an authenticated identity/config fetch (`apps/console/src/app/identity.tsx`), so a fresh Playwright context renders a blank app, which gives you invalid baselines. Seed a session once:

```bash
cd web
# 1. with the gateway running + an owner token available:
PW_TOKEN=<owner token> npx playwright test e2e/auth.setup.ts     # writes e2e/.auth/state.json
# 2. capture real baselines against the authenticated app:
STORAGE_STATE=e2e/.auth/state.json PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 npm run e2e:update
# 3. thereafter, verify zero-diff / AA-clean:
STORAGE_STATE=e2e/.auth/state.json PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 npm run e2e
```

Commit the captured `e2e/__screenshots__/*-<platform>.png` baselines. (`e2e/.auth/` is gitignored because it holds the token.)

**CI (plan 33 rails).** Mint a scoped test-owner token at gateway boot, pass it as `PW_TOKEN`, run `e2e/auth.setup.ts` in a global-setup, capture `-linux` baselines, then add an `e2e` step to the `web` job so the axe rail and the visual rail block regressions.

## What the unblock enables (the remaining S2/S3 backlog, worst first)

Once baselines exist, each item below is one screenshot-verified increment. `npm run e2e` has to stay zero-diff, and a real visual change gets `e2e:update` plus a plan Execution-log entry for owner review.

1. **`CodeCockpitPage.tsx`** (51 raw `<button>`) to `Button`/`IconButton`, in small increments. Ratchet the `rawButton` baseline down each commit.
2. **`ChatPage.tsx`** (34 raw `<button>`) the same way.
3. **Migrate the 206 raw form inputs** to `TextField`/`TextArea`. The primitive already exists. Ratchet `rawInput` down.
4. **Finish the inline font-weight migration** (168 to 0 where they are JSX inline; the CodeMirror-theme uses in `liveMarkdown.ts` stay). Ratchet `inlineFontWeight` down.
5. **Ad-hoc empty states** (`ChatActivityPanel.Empty`, `NotificationsPage.EmptyFeed`) to the canonical `EmptyState`/`SlotEmptyState`.
6. **Dark/light and responsive parity pass** (S3/T3.3). The visual suite already captures both themes; add a phone-viewport project to `playwright.config.ts` and assert no horizontal body scroll.

## Owner taste calls (plan Owner-tasks)

- Confirm the audit's worst-first priority, or reweight it to the surfaces you use most.
- Sign off on any consolidation that merges two visual patterns into one. None so far have changed pixels.
- The WCAG target stays **AA** (per PRODUCT.md), confirmed, not AAA.
