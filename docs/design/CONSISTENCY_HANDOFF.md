# Maintaining console consistency

Use the current [console design contract](../../apps/console/DESIGN.md), [product guidance](../../apps/console/PRODUCT.md), [patterns](PATTERNS.md) and [motion vocabulary](MOTION.md) when changing a surface. The [audit](CONSISTENCY_AUDIT.md) retains historical measurements; it is not a current backlog or a statement that CI is green.

## Shared controls

Native primitives live under `apps/console/src/shared/ui/`, and feature code under `apps/console/src/features/`. Reuse controls when their typed interfaces preserve the existing interaction, accessibility and layout. A specialized control whose semantics are not supported should not be disguised as primitive adoption through an alias.

Preserve actual callbacks, disabled conditions, recovery actions and authorization checks during migration. Distinguish an operation in progress from a missing prerequisite or pending selection. Explain disabled actions with accurate reasons, and provide meaningful labels for icon-only controls.

## Measurements and checks

The console contains static source rails and runtime tests; each measures a different property. A source count does not demonstrate keyboard usability, and a component test does not demonstrate a complete authenticated page journey.

The root `npm run audit:consistency` command invokes the configured report writer. Review its destination and generated contents before treating them as the current audit. Keep measured counts separate from historical values and do not adjust exclusions merely to hide incompatible controls.

For browser setup, commands and screenshot tolerances, use [the current E2E guide](../../apps/console/e2e/README.md). Authentication state contains credentials and must remain outside committed fixtures. Review baseline changes against actual rendered content; a blank authenticated shell is not a valid visual baseline. Passing a selected browser test qualifies that tested route, viewport, state and environment only.
