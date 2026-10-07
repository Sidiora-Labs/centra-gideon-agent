# Agent activity feed

The dashboard activity world renders a bounded projection of current sessions, loops,
and subagents. The shared data hook owns fetching and folding; the scene consumes the
result instead of inventing another connection or interpreting raw socket envelopes.

## Contract and sources

`apps/console/src/shared/data/useAgentActivity.ts` defines `AgentActivityFeed`:

```ts
interface AgentActivityFeed {
  entities: AgentActivityEntity[]
  truncated: number
  error: unknown
  loading: boolean
  refresh: () => void
}
```

Entities have a kind-prefixed ID, kind (`session`, `loop`, or `subagent`), title,
state, references, and optional progress. Current states are `working`, `needs_input`,
`waiting_approval`, `idle`, and `error`. Unknown progress is absent, not zero.

The hook reads the existing loops, chat sessions, spawn, and approvals APIs. Approval
session identity joins waiting approvals to the relevant work; that state takes
precedence over a generic busy status. Loop status folding uses effective status, held
and stop state, and reported errors rather than only the raw stored status string.

The projection caps entities at the current `MAX_ENTITIES` of 64 and limits quiet
sessions separately. `truncated` records what was omitted. Do not render a partial view
as the full number of agents or silently turn a failed read into an empty scene.

## Refresh and rendering

WebSocket messages signal a debounced refetch; they are not a second authoritative
activity payload. Reconnect and visibility-aware polling supply catch-up paths. Use the
current hook's timers instead of copying fixed timing constants into a new scene.

The first-party world lives in
`apps/console/src/features/dashboard/world/AgentWorld.tsx`, with scene modeling in
`apps/console/src/features/dashboard/world/worldScene.ts`. It receives folded data.
Scene motion respects reduced-motion behavior, and the accessible summary/fallback
must preserve the information when canvas drawing or animation is unavailable.

A source contract or structural rendering test does not establish live browser geometry,
refresh timing, assistive-technology behavior, or performance on every device. Verify
those paths separately when changing the scene.

## Extensions

This feed is a typed read contract, not an advertised third-party world installation
mechanism. A new app contribution needs an actual manifest/loader/host consumer before
it can be described as available. Adding an entity kind also affects folding, references,
state mapping, summaries, and consumers; a type declaration alone is insufficient.

Add a field at the shared contract and relevant fold, with a meaningful test of its
source and unavailable state. Keep endpoint fetching in the host data layer instead of
adding private fetches inside a scene.
