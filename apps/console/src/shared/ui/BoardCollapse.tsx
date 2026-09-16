import { useState, type HTMLAttributes } from 'react'
import { ChevronsRightLeft } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { fvs } from '../theme/fontWeight'


export const COLLAPSED_COL_WIDTH = '44px'

export const isBoardColumnCollapsed = (itemCount: number): boolean => itemCount === 0

export const boardGridTemplate = (collapsed: boolean[], minCol = '240px'): string =>
  collapsed.map((c) => (c ? COLLAPSED_COL_WIDTH : `minmax(${minCol}, 1fr)`)).join(' ')

export function useBoardCollapse(storageKey: string) {
  const [overrides, setOverrides] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || '{}') as Record<string, boolean> } catch { return {} }
  })
  const isCollapsed = (colId: string, itemCount: number): boolean =>
    overrides[colId] ?? isBoardColumnCollapsed(itemCount)
  const toggle = (colId: string, itemCount: number) => {
    setOverrides((prev) => {
      const target = !(prev[colId] ?? isBoardColumnCollapsed(itemCount))
      const next = { ...prev }
      if (target === isBoardColumnCollapsed(itemCount)) delete next[colId]
      else next[colId] = target
      try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch {   }
      return next
    })
  }
  return { isCollapsed, toggle }
}

export function CollapseColumnButton({ onCollapse }: { onCollapse: () => void }) {
  return (
    <button type="button" aria-label="Collapse column" title="Collapse column" aria-expanded
      onClick={(e) => { e.stopPropagation(); onCollapse() }}
      className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-highest hover:text-on-surface transition-colors hit-24">
      <ChevronsRightLeft size={13} />
    </button>
  )
}

export function CollapsedBoardColumn({ icon: Icon, label, count, tone, onExpand, ...rest }: {
  icon: LucideIcon; label: string; count: number
  tone?: string
  onExpand?: () => void
} & HTMLAttributes<HTMLDivElement>) {
  const activate = onExpand
    ? (e: React.KeyboardEvent) => {
        if (e.key !== 'Enter' && e.key !== ' ') return
        e.preventDefault()
        onExpand()
      }
    : undefined
  return (
    <div title={`${label} · ${count}${onExpand ? ' — click to expand' : ''}`} {...rest}
      onClick={onExpand}
      onKeyDown={activate}
      role={onExpand ? 'button' : undefined}
      tabIndex={onExpand ? 0 : undefined}
      aria-expanded={onExpand ? false : undefined}
      aria-label={onExpand ? `${label}, ${count} — expand column` : undefined}
      className={`flex min-h-0 flex-col items-center gap-1.5 rounded-xl px-1 py-2 transition-colors ${onExpand ? 'cursor-pointer hover:bg-surface-high focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary' : ''}`}>
      <Icon size={13} style={{ color: tone || 'var(--color-on-surface-low)' }} />
      <span data-type="caption" className="text-on-surface-low tabular-nums">{count}</span>
      <span data-type="caption" className="min-h-0 flex-1 truncate text-on-surface-low"
        style={{ writingMode: 'vertical-rl', ...fvs(550), color: tone || undefined }}>{label}</span>
    </div>
  )
}
