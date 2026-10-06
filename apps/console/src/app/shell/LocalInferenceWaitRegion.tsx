import { useState } from 'react'
import { api } from '../../shared/data/api'
import { useLocalInferenceWaits } from '../../shared/data/localInference'
import { Button } from '../../shared/ui/Button'
import { notify } from './appSdk'

export function LocalInferenceWaitRegion() {
  const { waits, refresh } = useLocalInferenceWaits()
  const [moving, setMoving] = useState('')
  if (!waits.length) return null
  const moveOn = async (id: string) => {
    setMoving(id)
    try { await api.localInferenceMoveOn(id) }
    catch (error) { notify(`Couldn't move on: ${String((error as Error)?.message || error)}`, 'error') }
    finally { setMoving(''); refresh() }
  }
  return <section aria-label="Waiting for local inference" aria-live="polite" className="border-b border-outline-variant/40 px-l py-m text-[0.8125rem]">
    {waits.map(wait => <div key={wait.id} className="flex flex-wrap items-center justify-between gap-m py-s">
      <div><strong>{wait.step}</strong><p className="text-on-surface-variant">Waiting for {wait.model} on {wait.provider}; {wait.holder} is using it. Position {wait.position}{wait.seconds_left !== null ? ` · ${Math.ceil(wait.seconds_left)} seconds left` : ''}.</p></div>
      {wait.next_ref && <Button disabled={!!moving} onClick={() => void moveOn(wait.id)}>Try next model</Button>}
    </div>)}
  </section>
}
