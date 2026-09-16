import { connect, readStoredGateway } from '../connection/session.mjs'
import { forgetActiveGateway } from '../connection/registry.mjs'
import { watchSafeAreaInsets } from '../platform/safe-area.mjs'

export { connect, connectFromScan, handOff, readStoredGateway } from '../connection/session.mjs'

export function start({ doc, view, storage, location }) {
  const nodes = Object.fromEntries(['shell', 'connect', 'gateway', 'status'].map((id) => [id, doc.getElementById(id)]))
  if (nodes.shell) watchSafeAreaInsets(doc, view, nodes.shell)

  function attempt(raw, remembered = false) {
    const result = connect({ raw, storage, location })
    if (nodes.status) {
      nodes.status.textContent = result.ok ? '' : result.message
      nodes.status.hidden = result.ok
    }
    if (!result.ok && remembered) forgetActiveGateway(storage)
    return result
  }

  nodes.connect?.addEventListener?.('submit', (event) => {
    event.preventDefault?.()
    attempt(nodes.gateway?.value)
  })

  const remembered = readStoredGateway(storage)
  if (remembered) {
    if (nodes.gateway) nodes.gateway.value = remembered
    attempt(remembered, true)
  }
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  let storage
  try { storage = window.localStorage } catch { /* Connecting remains available without persistence. */ }
  start({ doc: document, view: window, storage, location: window.location })
}
