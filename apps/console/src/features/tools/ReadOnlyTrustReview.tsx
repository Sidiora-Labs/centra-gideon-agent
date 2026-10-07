import { useState } from 'react'
import { api, type McpServer } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { Modal } from '../../shared/ui/Modal'
import { FieldError } from '../../shared/ui/forms'
import { invalidateKeys } from '../../shared/data/data'

export function ReadOnlyTrustControls({ server }: { server: McpServer }) {
  const [review, setReview] = useState<McpServer | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const trust = server.readOnlyTrust
  if (!trust) return null
  const refresh = () => { invalidateKeys('tools:servers'); invalidateKeys('tools:index') }
  const stop = async () => {
    setBusy(true); setError('')
    try { await api.revokeMcpReadOnlyTrust(server.name); refresh() }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Could not stop trusting this server') }
    finally { setBusy(false) }
  }
  const allow = async () => {
    const shown = review?.readOnlyTrust
    if (!review || !shown?.listed || !shown.configurationRevision || !shown.catalogRevision) return
    setBusy(true); setError('')
    try {
      await api.trustMcpReadOnlyDefinitions(review.name, shown.listed, shown.configurationRevision, shown.catalogRevision)
      setReview(null); refresh()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'The reviewed inventory could not be trusted'); refresh() }
    finally { setBusy(false) }
  }
  const changes = [...(trust.added ?? []).map(name => `${name} added`), ...(trust.changed ?? []).map(item => `${item.name}: ${item.parts.join(', ')} changed`), ...(trust.removed ?? []).map(name => `${name} removed`)]
  return <div className="mb-m rounded-md border border-outline-variant/25 bg-surface-container/30 p-m grid gap-s">
    <div className="flex flex-wrap items-center gap-s"><span className="text-on-surface text-[0.8125rem]">Read-only labels: {trust.trusted ? 'reviewed definitions trusted' : 'not trusted'}</span>
      <Button size="sm" variant="secondary" disabled={busy || !trust.listed || server.allowed === false} onClick={() => { setError(''); setReview(server) }} disabledReason={busy ? 'Wait for the current trust operation to finish' : !trust.listed ? 'The MCP server must be listed before reviewing labels' : server.allowed === false ? 'Allow this MCP server before reviewing labels' : undefined}>{trust.trusted ? 'Review tool definitions' : 'Review read-only labels'}</Button>
      {trust.trusted && <Button size="sm" variant="ghost" loading={busy} onClick={stop}>Stop trusting labels</Button>}
    </div>
    <p className="text-on-surface-low text-[0.8125rem]">New or changed definitions ask for approval until you review them. Server connection permission remains separate.</p>
    {changes.length > 0 && <ul className="list-disc pl-l text-warn text-[0.8125rem]">{changes.map(change => <li key={change}>{change}</li>)}</ul>}
    {!review && error && <FieldError>{error}</FieldError>}
    {review && <Modal title={`Review ${review.name}'s read-only labels`} onClose={() => !busy && setReview(null)}>
      <div className="p-l grid gap-m">
        <p className="text-on-surface text-[0.9375rem]">Allow the exact definitions shown here to run without asking when this server labels them read-only. A tool that changes something while claiming to be read-only could make that change without approval.</p>
        <p className="text-on-surface-low text-[0.8125rem]">Only these reviewed definitions receive this authority. New tools, changed definitions and changed server configuration require another review.</p>
        <div className="grid gap-s">{review.tools.filter((tool): tool is Exclude<typeof tool, string> => typeof tool !== 'string').map(tool => <section key={tool.name} className="rounded-md border border-outline-variant/25 bg-surface-container/30 p-m grid gap-s">
          <div className="flex flex-wrap gap-s items-center"><h3 className="text-on-surface text-[0.9375rem]">{tool.name}</h3><span className="text-on-surface-low text-[0.75rem]">{tool.annotations?.readOnlyHint === true ? 'Claims read-only' : 'Still asks for approval'}</span></div>
          <p className="whitespace-pre-wrap text-on-surface-low text-[0.8125rem]">{tool.description || 'No description provided.'}</p>
          {tool.annotations?.destructiveHint === true && <p className="text-danger text-[0.8125rem]">Also declares destructive effects.</p>}
          {tool.annotations?.openWorldHint === true && <p className="text-warn text-[0.8125rem]">May interact with external systems.</p>}
          <details><summary className="cursor-pointer text-on-surface-low text-[0.8125rem]">Review input schema and labels</summary><pre className="mt-s overflow-x-auto whitespace-pre-wrap break-all text-[0.75rem] text-on-surface-low">{JSON.stringify({ inputs: tool.inputSchema, labels: tool.annotations }, null, 2)}</pre></details>
        </section>)}</div>
        {error && <FieldError>{error}</FieldError>}
        <div className="flex justify-end gap-s"><Button variant="ghost" onClick={() => setReview(null)} disabled={busy} disabledReason={busy ? 'Wait for the trust review to finish saving' : undefined}>Cancel</Button><Button loading={busy} onClick={allow}>Allow reviewed read-only definitions</Button></div>
      </div>
    </Modal>}
  </div>
}
