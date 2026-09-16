import { useEffect, useRef } from 'react'
import { List } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import type { OutlineEntry } from './readingOutline'

export function DocumentOutline({ entries, activeOffset, onSelect }: {
  entries: OutlineEntry[]
  activeOffset: number | null
  onSelect: (entry: OutlineEntry) => void
}) {
  const rows = useRef(new Map<number, HTMLLIElement>())

  useEffect(() => {
    if (activeOffset === null) return
    rows.current.get(activeOffset)?.scrollIntoView?.({ block: 'nearest' })
  }, [activeOffset])

  const shown = entries.filter((e) => e.text)
  if (!shown.length) return null

  return (
    <nav aria-label="Document outline" className="flex min-h-0 flex-col">
      <div data-type="caption" className="mb-1.5 flex items-center gap-1.5 text-on-surface-low uppercase tracking-wide">
        <List size={12} />Outline
      </div>
      {
}
      <ol className="flex min-h-0 flex-1 flex-col gap-xs overflow-y-auto">
        {shown.map((e) => (
          <li key={e.offset} ref={(el) => { if (el) rows.current.set(e.offset, el); else rows.current.delete(e.offset) }}>
            <Button
              variant={e.offset === activeOffset ? 'tonal' : 'ghost'}
              size="xs"
              shape="squircle"
              onClick={() => onSelect(e)}
              ariaPressed={e.offset === activeOffset}
              title={e.text}
              className="w-full !justify-start px-2"
            >
              <span className="flex-1 truncate text-left" style={{ paddingInlineStart: e.depth * 12 }}>{e.text}</span>
            </Button>
          </li>
        ))}
      </ol>
    </nav>
  )
}
