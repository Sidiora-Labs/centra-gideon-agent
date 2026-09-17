> [!IMPORTANT]
> **Run `sh tooling/scripts/install_git_hooks.sh` once in your clone.** It installs the
> git hooks, which format staged Python with `black`/`isort` and sign your commits off
> for DCO before they are written. That clears the two checks contributors hit most.
>
> **Before pushing:** `make format && make lint` (Python) and, if you touched `apps/console/`,
> `npm run typecheck:web && npm run test:web`.
>
> **Every commit must be signed off** (DCO) or CI fails. Commit with `git commit -s`.
> Forgot on commits you already pushed? Fix and re-push:
> ```bash
> git rebase --signoff main && git push --force-with-lease
> ```
> The sign-off name/email must match your commit author. See [CONTRIBUTING.md](../CONTRIBUTING.md#developer-certificate-of-origin-dco).

<!--
Thanks for the PR. Fill in the four sections below. They mirror how we decide a
change is done (see CONTRIBUTING.md). A reviewer checks a PR at a glance against
these.
-->

## What changed

<!-- One paragraph. What behavior/code changed and why. -->

## Change class

<!--
R / B / S (see ../CONTRIBUTING.md#breaking-changes):
- R (reversible): nothing persisted and no stable surface changes.
- B (behavioral): changes a stable surface (API/CLI/config) or persisted state.
- S (schema): changes a stored schema or another stable contract.

Aim for class R. If your change is B or S, describe the break here and add a
CHANGELOG entry. Do not build compatibility shims or migration helpers: there is
no migration machinery yet, and that is deliberate. The maintainer decides
whether to take the break, reshape it, or schedule it.
-->

Class: <!-- R | B | S -->

## What you validated as a user

<!--
Not just what you wrote, but what you drove. Which flows in the UI or CLI, what
you checked in logs and persisted state. A passing unit test is not a user path.
"Ran the endpoint" is not validation.
-->

## Docs touched

<!--
Config fields, routes, CLI flags, or user-visible behavior: docs move in the same
PR (docs/reference/, docs/guides/). A class B or S change also needs a CHANGELOG
entry. Write "none" only if genuinely none apply.
-->
