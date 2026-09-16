import { useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { PreviewProps } from './contentTypes'
import { loadInfographicEngine, type InfographicInstance } from './antvEngine'

type RenderStatus = 'loading' | 'rendered' | 'failed'
export function InfographicView({ content, mode }: PreviewProps) {
  const host = useRef<HTMLDivElement>(null)
  const engine = useRef<InfographicInstance | null>(null)
  const renderFault = useRef(false)
  const latestContent = useRef(content)
  latestContent.current = content
  const [status, setStatus] = useState<RenderStatus>('loading')
  const draw = () => {
    if (!engine.current) return
    renderFault.current = false
    try { engine.current.render(latestContent.current); setStatus(renderFault.current ? 'failed' : 'rendered') }
    catch { setStatus('failed') }
  }
  useEffect(() => {
    let cancelled = false
    setStatus('loading')
    void loadInfographicEngine().then(Constructor => {
      if (cancelled || !host.current) return
      try {
        const instance = new Constructor({ container: host.current, width: '100%', height: '100%', theme: mode })
        engine.current = instance
        instance.on?.('error', () => { if (!cancelled && engine.current === instance) { renderFault.current = true; setStatus('failed') } })
        draw()
      } catch { setStatus('failed') }
    }).catch(() => { if (!cancelled) setStatus('failed') })
    return () => {
      cancelled = true
      const previous = engine.current
      engine.current = null
      try { previous?.destroy() } catch { /* A failed render can already have disposed the engine. */ }
    }
  }, [mode])
  useEffect(draw, [content])
  return <div className="relative h-full w-full" data-infographic-status={status}>
    <div ref={host} hidden={status === 'failed'} className="flex h-full w-full items-center justify-center p-l [&_svg]:max-h-full [&_svg]:max-w-full" />
    {status === 'failed' && <pre data-type="body-s" className="m-l overflow-auto whitespace-pre-wrap rounded-xl border border-outline/30 bg-surface-container/30 px-m py-3 font-mono leading-relaxed text-on-surface-low">{content}</pre>}
    {status === 'loading' && <div className="pointer-events-none absolute inset-0 flex items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>}
  </div>
}
