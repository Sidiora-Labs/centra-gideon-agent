import { useEffect, useSyncExternalStore } from 'react'
import { AlertTriangle, Layers } from 'lucide-react'
import { GenUiWidget } from '../genui/GenUiWidget'
import { LayerBoundary } from './LayerBoundary'
import { LAYER_USER } from './layers'
import { loadSurfaceOverlays, overlayRefusalsFor, overlaysFor, subscribeSurfaceOverlays, surfaceOverlaySnapshot } from './overlay'

export function SurfaceOverlay({ surface }: { surface: string }) {
  useSyncExternalStore(subscribeSurfaceOverlays, surfaceOverlaySnapshot, surfaceOverlaySnapshot)
  useEffect(() => { void loadSurfaceOverlays() }, [])
  const content = overlaysFor(surface)
  const refusals = overlayRefusalsFor(surface)
  if (content.length + refusals.length === 0) return null
  return <section className="flex min-w-0 flex-col gap-m rounded-xl border border-outline-variant/30 p-m" data-testid="surface-overlay">
    <header className="flex items-center gap-s">
      <Layers size={14} aria-hidden className="shrink-0 text-primary" />
      <h2 data-type="label-l" className="text-on-surface-var">Yours</h2>
    </header>
    {refusals.map(({ file, error }) => <div key={file} role="alert" data-testid="overlay-refusal" data-type="caption"
      className="flex items-start gap-2 rounded-lg border border-warn/25 bg-warn/5 px-3 py-2 text-warn">
      <AlertTriangle size={13} aria-hidden className="mt-0.5 shrink-0" />
      <span className="min-w-0 break-words"><span className="font-mono">{file}</span> was refused — {error.what} {error.fix}</span>
    </div>)}
    {content.map((overlay) => <LayerBoundary key={overlay.file} layer={LAYER_USER} what={overlay.title || overlay.file}>
      <GenUiWidget content={overlay.body} title={overlay.title || overlay.file} />
    </LayerBoundary>)}
  </section>
}
