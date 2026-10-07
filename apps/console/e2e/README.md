# Console browser checks

Playwright exercises the served console with an authenticated gateway. The suite includes
functional journeys, screenshots, and axe checks; these have different evidence scopes.
A screenshot match is not proof of an external effect, and an automated axe result is not
complete WCAG conformance or screen-reader qualification.

## Setup and commands

Use the repository development environment and root workspace installation. Install the
required Playwright browser through the normal project setup. From `apps/console`:

```bash
npm run e2e
npm run e2e:visual
npm run e2e:a11y
npm run e2e:update
npm run e2e:report
```

`e2e` runs nonvisual cases followed by visual-tagged cases. `e2e:update` regenerates
screenshots; review intended visual changes rather than updating a baseline merely to
hide an unexpected regression. The current screenshot comparison allows a configured
pixel-difference ratio, not an unconditional zero-pixel-difference rule.

## Gateway and authentication

`playwright.config.ts` starts a gateway in a temporary home/workspace and a built console
preview. It seeds an onboarded identity, includes the actual app-UI fixture, and uses the
scripted model fixture for offline chat paths. Readiness captures the gateway token;
`auth.setup.ts` performs the authenticated handshake through the preview origin and
stores the cookie state for dependent cases.

This exercises real gateway auth and route consumers with scripted model responses.
It does not establish live model inference, vendor credentials, or a production app
installation journey beyond what each case actually performs.

The gateway command selects `../../.venv/bin/gideon` when executable, otherwise `gideon`
from PATH. It explicitly points Python imports at this checkout's runtime. Missing
backend prerequisites fail startup rather than proving a backendless page is healthy.

To use an existing isolated server instead:

```bash
PW_TOKEN="$GIDEON_TEST_TOKEN" PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 npm run e2e
```

Use a test gateway, never a real user's home. Tokens and stored auth state are credentials;
keep them out of tracked files and report output.

## Current controls and limits

`PW_PORT`, `PW_GATEWAY_PORT`, `PW_BASE_URL`, and `STORAGE_STATE` select preview/gateway and
auth state locations. `PW_CHROMIUM_EXECUTABLE_PATH` explicitly overrides the browser;
otherwise Playwright uses its installed default. The config has separate setup and
Chromium projects. Some live-model cases require explicit selection, and the onboarding
geometry case is excluded by the current config.

`routes.ts` defines the route capture list; it must follow actual navigation as routes
change. Screenshot paths carry platform suffixes. Linux is the declared primary baseline
platform; macOS images are supplemental. Default screenshot settings suppress animation
and hide the caret, so those images do not qualify motion behavior.

Helpers assert that the authenticated shell mounted before route checks. A fresh gateway
may show empty data states, which test shell chrome rather than populated workflows.
A skip must retain its specific reason; successful collection or an empty axe result on
onboarding is not proof that the intended page was exercised.

This README describes configuration and commands. It does not assert a current full
browser-suite pass, deployed behavior, or verified geometry on all devices.
