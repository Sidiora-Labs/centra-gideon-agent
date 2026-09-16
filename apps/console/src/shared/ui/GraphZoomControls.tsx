import { Plus, Minus, Maximize2 } from 'lucide-react'

export function GraphZoomControls({ onZoomIn, onZoomOut, onReset }: {
  onZoomIn: () => void
  onZoomOut: () => void
  onReset: () => void
}) {
  return (
    <div className="absolute bottom-3 right-3 flex flex-col gap-1 rounded-lg bg-surface-high/90 p-1 backdrop-blur">
      <button type="button" onClick={onZoomIn} title="Zoom in" aria-label="Zoom in"
        className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Plus size={15} /></button>
      <button type="button" onClick={onZoomOut} title="Zoom out" aria-label="Zoom out"
        className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Minus size={15} /></button>
      <button type="button" onClick={onReset} title="Reset view" aria-label="Reset view"
        className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Maximize2 size={14} /></button>
    </div>
  )
}
