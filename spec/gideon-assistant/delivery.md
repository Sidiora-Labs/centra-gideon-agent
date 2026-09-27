# Gideon assistant delivery

Build an assistant web entry that preserves Gideon's existing sign-in, session, and distribution
behavior. The first slice supplies a usable bootstrap contract to the conversation and workspace
features. Later slices package the finished interface for the OSS web server and current Windows
desktop shell.

This list specifies implementation work. Its paths are repository relative; new paths are
proposed. Native phone releases are a separate platform effort.

## Sequential implementation tasks

- [ ] **1. Establish the web bootstrap and identity boundary.** Create
    `apps/assistant/src/shared/bootstrap.web.tsx`, `apps/assistant/src/shared/auth.web.tsx`, and
    `apps/assistant/src/shared/transport.web.ts` for the new entry. Read the distribution
    configuration before rendering any protected screen. Preserve Gideon's existing owner
    sign-in, optional second factor, session cookie, and no-login setup behavior. Send API
    requests to same-origin Gideon endpoints with the existing version and session headers. A
    missing, expired, or rejected session presents an accessible sign-in or recovery state and
    preserves the intended route; it never shows a stale private workspace. This task has no
    shell dependency. Acceptance: a signed-in owner reaches a real read-only session, a
    signed-out owner cannot, and renewal or sign-out clears account-owned client state. Add
    focused bootstrap and transport tests at `apps/assistant/src/shared/bootstrap.test.tsx` and
    `apps/assistant/src/shared/transport.test.ts`.

- [ ] **2. Connect the assistant entry to the shared route and screen frame.** Add
    `apps/assistant/src/features/delivery/entry.web.tsx` and
    `apps/console/src/app/shell/assistantRouteBridge.ts` after the `shell` foundation. Preserve
    `#/...` route, subroute, query, and return destination across reload, sign-in, and workspace
    launch. Keep the old console available during the staged route migration and label any
    temporary handoff accurately. A direct link opens the requested record or a clear
    unavailable/permission state; browser Back returns to the previous context. Depend on
    `shell-04` routing and delivery task 1. Acceptance: chat, one workspace, and a direct deep link share
    identity and route state without creating a second conversation owner. Test
    `apps/assistant/src/features/delivery/entry.test.tsx` with real route parsing.

- [ ] **3. Package the assistant web artifact for OSS.** Update `apps/assistant/package.json`,
    `apps/assistant/app.json`, `apps/console/package.json`, `Makefile`, `setup.py`,
    `infrastructure/docker/Dockerfile.web`, and
    `runtime/gideon/interfaces/dashboard/handlers/core.py` so the exported web entry and assets
    are delivered by the current OSS package paths. Keep the initial assistant entry on the same
    origin, with explicit asset base paths and no required hard-coded service URL. The server
    must return the entry with the correct content type and a useful unavailable page when its
    artifact is missing. Depend on tasks 1–2. Acceptance: a clean OSS package serves the
    assistant route, its fonts/images/chunks and an existing console fallback; reload on a
    nested route resolves to the correct app. Test `checks/runtime/test_assistant_entry.py` and
    `apps/assistant/src/features/delivery/assets.test.ts` against packaged output.

- [ ] **4. Migrate the root service worker and install metadata.** Update
    `apps/console/src/app/background/service-worker.ts`,
    `apps/console/src/app/shell/swPolicy.ts`,
    `apps/console/src/app/shell/registerServiceWorker.ts`,
    `apps/console/tooling/buildServiceWorker.mjs`, and `apps/console/index.html` for the
    assistant artifact. Keep one worker at `/sw.js` with root scope, correct asset-versioning,
    notification click behavior, and authenticated manifest/icon responses. A stale worker must
    not strand a returning owner on a previous entry. Offline navigation has an honest cached
    fallback; sign-out does not expose another owner's content. Depend on task 3. Acceptance:
    first load, update, offline return, notification click, and missing manifest/worker
    resources produce the expected document and MIME types. Extend
    `apps/console/src/app/shell/registerServiceWorker.test.ts` and
    `apps/console/src/app/shell/manifest.test.ts`.

- [ ] **5. Preserve links, streams, downloads, and connection returns.** Complete
    `apps/assistant/src/shared/transport.web.ts`,
    `apps/assistant/src/features/delivery/resources.web.ts`,
    `apps/assistant/src/features/delivery/returnRoute.web.ts`, and
    `apps/console/src/app/shell/assistantRouteBridge.ts`. Use same-origin, owner-scoped resource
    URLs for embedded readers and downloads. Handle reconnect for live streams and restore
    route/query after connection authorization. Never encode session credentials in copied
    links. An inaccessible resource gets a clear error and return action; an interrupted
    download or stream can be retried without duplicate work. Depend on tasks 2–3 and
    `conversation` baseline. Acceptance: direct record links, embedded resources, stream
    reconnect, download, and connection return work for the same owner, including after reload.
    Test `apps/assistant/src/features/delivery/resources.test.ts` and
    `apps/assistant/src/features/delivery/returnRoute.test.ts`.

- [ ] **6. Carry the web experience through the Windows shell.** Update
    `apps/desktop/src/application/endpoint-session.js`,
    `apps/desktop/src/application/window-workspace.js`, and
    `apps/desktop/src/connection/hosted-config.js` only as needed for the assistant route and
    return flow. Preserve the configured HTTPS origin, same-origin navigation, sign-in callback
    window, download behavior, and remote-page sandbox. Keep a path or hash in navigation state
    rather than turning it into an endpoint setting. A connection failure offers retry or
    gateway selection while preserving the intended destination. Depend on task 5. Acceptance:
    the existing Windows app opens the assistant entry, follows a direct link, returns from
    sign-in, downloads a file, and does not attach privileged local APIs to remote content. Test
    `apps/desktop/test/assistantNavigation.test.js`; record separate native Windows evidence
    before a desktop release claim.

- [ ] **7. Qualify responsive distribution and stage the cutover.** Add
    `apps/assistant/e2e/delivery.spec.ts` and
    `apps/assistant/src/features/delivery/cutover.web.ts` to exercise the packaged product at
    phone, tablet, and desktop widths with keyboard and touch. Cover sign-in, direct link, chat
    read/write, one workspace, stream recovery, download, sign-out, service-worker update, and
    the console fallback. Keep a reversible route switch until equivalent journeys are proven;
    record gaps instead of treating a rendered page as feature completion. Depend on tasks 3–6
    and completed `shell`, `conversation`, `activity`, `discovery`, `personal`,
    `communications`, `work`, `code`, `browser`, `library`, and `studio` journeys. Acceptance:
    each supported width has readable controls, no clipped critical action, restored route
    context, visible loading/error states, and evidence tied to the exact artifact revision.

## Shared acceptance

The assistant entry consumes Gideon's canonical records and existing permission decisions.
Distribution configuration changes presentation and routing, while server authorization remains
authoritative. A readable message explains unavailable capabilities, missing assets, expired
identity, denied resources, and interrupted connections. Every interactive recovery action has a
keyboard focus target and an announced status; direct links remain usable after refresh. Browser
and Windows results are recorded independently. The OSS artifact can be built and served without
a service-specific account, and future distribution adaptations consume the same implementation
source.
