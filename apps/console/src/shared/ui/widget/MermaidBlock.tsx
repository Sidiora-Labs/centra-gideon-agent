import { useEffect, useRef, useState } from 'react'
import { useMode } from '../../../app/shell/theme'
import { renderWidgetDiagram } from './diagramRenderer'

export function MermaidBlock({ code }: { code: string }) {
  const { mode } = useMode()
  const canvas = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)
  const request = useRef(0)
  useEffect(() => {
    const revision = ++request.current
    setFailed(false)
    renderWidgetDiagram(code, mode).then(svg => {
      if (revision !== request.current || !canvas.current) return
      canvas.current.replaceChildren(document.createRange().createContextualFragment(svg))
    }).catch(() => { if (revision === request.current) setFailed(true) })
    return () => { request.current++ }
  }, [code, mode])
  return <div className="my-3 rounded-xl border border-outline-variant/40 bg-surface-low">
    <div ref={canvas} hidden={failed} className={failed ? 'hidden' : 'flex justify-center overflow-x-auto p-3'} />
    {failed && <pre tabIndex={0} role="group" aria-label="Diagram source" data-type="body-s" className="overflow-x-auto p-3"><code className="font-mono text-on-surface-low">{code}</code></pre>}
  </div>
}
