export interface PromptUsageRecord {
  version: number
  provider: string
  name: string
  consumers: { kind: 'binding' | 'native' | 'app'; id: string; label: string }[]
  total: number
  complete: boolean
  deletable: boolean
}

export function deletionBlockedReason(usage?: PromptUsageRecord): string | undefined {
  if (!usage?.complete) return 'Prompt dependencies are unavailable. Refresh before deleting.'
  if (!usage.deletable) return 'Remove active bindings or owning declarations before deleting this prompt.'
  return undefined
}

export function PromptUsage({ usage }: { usage?: PromptUsageRecord }) {
  return <section aria-label="Prompt dependencies" className="grid gap-s rounded border border-outline-variant p-m">
    <h2 className="text-on-surface">Prompt dependencies</h2>
    {!usage?.complete && <p role="status">Dependencies could not be read. Deletion is unavailable until the binding store can be inspected.</p>}
    {usage?.complete && usage.total === 0 && <p>No active bindings or declared consumers.</p>}
    {!!usage?.total && <>
      <p>{usage.total} consumers. Active bindings can be changed in Settings → Prompts. Native and app declarations remain protected even when overridden.</p>
      <ul>{usage.consumers.map(consumer => <li key={`${consumer.kind}:${consumer.id}`} className="break-words">{consumer.label} · {consumer.kind} · {consumer.id}</li>)}</ul>
      {usage.total > usage.consumers.length && <p>Showing the first {usage.consumers.length} consumers.</p>}
    </>}
  </section>
}
