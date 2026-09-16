import { Database, Plus, Tag } from 'lucide-react'
import { Eyebrow } from '../../shared/ui/Eyebrow'
import { Button } from '../../shared/ui/Button'
import { FilterRow } from '../../shared/ui/FilterRow'

export interface RailOption {
  key: string
  label: string
  count: number
}

export function StoreSideRail({
  categories, category, onCategory, categoryTotal,
  sources, source, onSource, sourceTotal,
  onAddSource,
}: {
  categories: RailOption[]
  category: string
  onCategory: (key: string) => void
  categoryTotal: number
  sources: RailOption[]
  source: string
  onSource: (key: string) => void
  sourceTotal: number
  onAddSource: () => void
}) {
  return (
    <nav aria-label="Categories and sources" className="flex w-[13rem] shrink-0 flex-col gap-l">
      <StoreRailBlock title="Categories" allLabel="All apps" allCount={categoryTotal} icon={Tag}
        options={categories} value={category} onChange={onCategory} />
      <StoreRailBlock title="Sources" allLabel="All sources" allCount={sourceTotal} icon={Database}
        options={sources} value={source} onChange={onSource} />
      <div className="px-1">
        <Button variant="ghost" size="sm" onClick={onAddSource}>
          <Plus size={14} /> Add source
        </Button>
      </div>
    </nav>
  )
}

function StoreRailBlock({ title, allLabel, allCount, icon, options, value, onChange }: {
  title: string
  allLabel: string
  allCount: number
  icon: typeof Tag
  options: RailOption[]
  value: string
  onChange: (key: string) => void
}) {
  const rows: RailOption[] = [{ key: 'all', label: allLabel, count: allCount }, ...options]
  return (
    <div className="flex flex-col gap-0.5">
      {
}
      <Eyebrow as="h2" className="px-2 pb-0.5">{title}</Eyebrow>
      {rows.map((o) => (
        <FilterRow key={o.key} label={o.label} count={o.count} icon={icon}
          selected={value === o.key} pressed={value === o.key}
          indicatorId={`store-rail-${title}`} onClick={() => onChange(o.key)} />
      ))}
    </div>
  )
}
