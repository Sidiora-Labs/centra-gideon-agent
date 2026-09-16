import type { ReactNode } from 'react'
import { Segmented, type SegOption } from './Segmented'
import { StaleNotice } from './StaleNotice'
import { SearchField } from './SearchField'
import { resultMessage, searchControlName, type ResultCount } from './interactionState'

type SearchControl = { value: string; onChange: (value: string) => void; placeholder?: string; label?: string; autoFocus?: boolean }
type FilterControl = { value: string; onChange: (value: string) => void; options: SegOption[]; ariaLabel?: string }

export function ListControls({ search, filter, results, stale, children }: {
  search?: SearchControl
  filter?: FilterControl
  results?: { count: number; noun: string; active: boolean }
  stale?: boolean
  children?: ReactNode
}) {
  if (!search && !filter && !children) return null
  const controls: ReactNode[] = []
  if (search) controls.push(
    <div key="search" className="min-w-[12rem] flex-1">
      <SearchField value={search.value} onChange={search.onChange} autoFocus={search.autoFocus}
        placeholder={search.placeholder ?? 'Search'} ariaLabel={search.label ?? search.placeholder ?? 'Search'}
        name={searchControlName(search.label ?? search.placeholder ?? 'list')} />
    </div>,
  )
  if (filter) controls.push(
    <Segmented key="filter" {...filter} ariaLabel={filter.ariaLabel ?? 'Filter'} />,
  )
  return <div className="shrink-0 border-b border-outline-variant/30 bg-surface/40">
    <div className="mx-auto flex w-full flex-wrap items-center gap-m px-l py-m" style={{ maxWidth: 'var(--content-width)' }}>
      {controls}{children}
      {results && <StaleNotice stale={!!stale} what={results.noun} className="ml-auto" />}
    </div>
    <ResultAnnouncement {...(results ?? { count: 0, noun: '', active: false })} />
  </div>
}

export function ResultAnnouncement(result: ResultCount) {
  return <div role="status" aria-live="polite" className="sr-only">{resultMessage(result)}</div>
}
