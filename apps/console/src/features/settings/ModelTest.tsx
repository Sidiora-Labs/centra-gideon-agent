import { useEffect, useRef, useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { api, type AvailableModel, type ModelTestResult } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'

const CHAT_CASES = new Set(['chat', 'code_tools', 'reasoning', 'background', 'orchestration', 'loops'])
export function modelTestRefusal(useCase: string, model: AvailableModel): string {
  if (model.downloaded === false) return 'Download this model before testing it.'
  if (model.untestable?.[useCase]) return model.untestable[useCase]
  if (!CHAT_CASES.has(useCase)) return 'A small Test for this use case is not available yet.'
  if (!model.capabilities.includes('chat')) return 'This model is not listed for chat.'
  return ''
}

export function ModelTest({ useCase, model }: { useCase: string; model: AvailableModel }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ModelTestResult | null>(null)
  const identity = `${useCase}\n${model.provider}:${model.id}`
  const current = useRef(identity), mounted = useRef(true)
  current.current = identity
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => { setResult(null); setBusy(false) }, [identity])
  const refusal = modelTestRefusal(useCase, model)
  const run = async () => {
    const requested = identity
    setBusy(true); setResult(null)
    try {
      const answer = await api.modelTest(useCase, `${model.provider}:${model.id}`)
      if (mounted.current && current.current === requested) setResult(answer)
    } catch (error) {
      if (mounted.current && current.current === requested) setResult({ ok: false, detail: String((error as Error)?.message || error), reason: 'request_failed', duration_ms: 0 })
    } finally {
      if (mounted.current && current.current === requested) setBusy(false)
    }
  }
  if (refusal) return <p data-type="caption" className="px-3 pb-2 text-on-surface-low">No Test: {refusal}</p>
  return <div className="flex flex-wrap items-center gap-2 px-3 pb-2">
    <Button variant="tonal" size="xs" loading={busy} loadingLabel="Testing…" disabled={busy} onClick={() => void run()} ariaLabel={`Test ${model.name} for ${useCase}`}><FlaskConical size={10} aria-hidden /> Test</Button>
    {result && <span role={result.ok ? 'status' : 'alert'} data-type="caption" className={result.ok ? 'text-on-surface-low' : 'text-danger'}><span className="sr-only">{result.ok ? 'Test passed: ' : 'Test failed: '}</span>{result.detail}</span>}
  </div>
}
