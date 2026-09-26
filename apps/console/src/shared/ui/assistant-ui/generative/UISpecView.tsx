import { useState } from 'react'
import { renderGenerativeUI } from '@assistant-ui/react-generative-ui'
import { styledGenerativeUILibrary } from './vendor/generative-ui'
import { dispatchUISpecAction, type ActionCapabilities, type ActionResult } from './actions'
import { planUISpecRender } from './adapter'
import type { LiveUISpec } from './uispec'
import './uispec.css'

export function UISpecView({ spec, capabilities = {}, onActionResult }: {
  spec: LiveUISpec
  capabilities?: ActionCapabilities
  onActionResult?: (result: ActionResult) => void
}) {
  const [message, setMessage] = useState('')
  const plan = planUISpecRender(spec, capabilities)
  const dispatch = async (action: unknown) => {
    const result = await dispatchUISpecAction(spec, action, capabilities)
    setMessage(result.message)
    onActionResult?.(result)
  }
  return <section data-gideon-uispec={spec.template} aria-label={`Live ${spec.template} result`}>
    {renderGenerativeUI(plan.tree, styledGenerativeUILibrary, { status: 'done', dispatch })}
    {message && <p role={message === 'Record opened.' ? 'status' : 'alert'}>{message}</p>}
  </section>
}
