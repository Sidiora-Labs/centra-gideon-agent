import { useId } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Check, SlidersHorizontal, type LucideIcon } from 'lucide-react'
import { accentChip } from '../theme/accent'
import { fvs } from '../theme/fontWeight'
import { physics, spring } from '../theme/motion'
import { Button } from './Button'
import { FilterRow } from './FilterRow'
import { Popover } from './Popover'
import { TextLink } from './TextLink'
import { filterState } from './filterState'

export interface FilterOption { key: string; label: string; icon?: LucideIcon; count?: number; groupLabel?: string }
export interface FilterSectionDef { title: string; value: string; defaultKey: string; options: FilterOption[]; onChange: (key: string) => void }

export function FilterMenu({ sections, label = 'Filter', align = 'right' }: {
  sections: FilterSectionDef[]; label?: string; align?: 'left' | 'right'
}) {
  const identity = useId()
  const reduced = useReducedMotion()
  const state = filterState(sections)
  const absent = reduced ? { opacity: 0 } : { opacity: 0, scale: 0.6 }
  return <Popover portal align={align} width={264} placement="bottom" trigger={(open, toggle) => <button type="button"
    onClick={toggle} aria-label="Filter & sort" title="Filter & sort" aria-expanded={open} data-type="body-s"
    className={`relative inline-flex h-10 items-center gap-s rounded-lg border border-outline-variant/30 px-l transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${state.activeCount || open ? '' : 'bg-surface-container text-on-surface-var hover:bg-surface-high'}`}
    style={state.activeCount || open ? accentChip : undefined}>
    <SlidersHorizontal size={14} aria-hidden /><span className="hidden sm:inline">{label}</span>
    <AnimatePresence>{state.activeCount > 0 && <motion.span initial={absent} animate={{ opacity: 1, scale: 1 }} exit={absent}
      transition={reduced ? spring.effects : physics.playful} data-type="caption" style={fvs(600)}
      className="inline-flex h-[1.05rem] min-w-[1.05rem] items-center justify-center rounded-pill bg-primary px-1 text-on-primary tabular-nums">{state.activeCount}</motion.span>}</AnimatePresence>
  </button>}>
    {(close) => <div className="flex max-h-[70vh] flex-col gap-m overflow-y-auto">
      {state.groups.map((group) => {
        const groupId = `${identity}-${group.identity}`
        return <section key={group.identity} aria-labelledby={groupId} className="flex flex-col gap-0.5">
          <header className="flex items-center justify-between gap-s border-b border-outline-variant/20 px-s pb-s">
            <h3 id={groupId} data-type="caption" className="uppercase tracking-wide text-on-surface-low">{group.title}</h3>
            {group.active && <TextLink onClick={() => group.onChange(group.defaultKey)} size="xs">Clear</TextLink>}
          </header>
          {group.options.map((option) => {
            const selected = option.key === group.value
            return <div key={option.key}>
              {option.groupLabel && <div data-type="caption" className="mt-s border-t border-outline-variant/20 px-s pt-s uppercase tracking-wide text-on-surface-low">{option.groupLabel}</div>}
              <FilterRow label={option.label} count={option.count} icon={option.icon} selected={selected} pressed={selected}
                indicatorId={groupId} onClick={() => group.onChange(option.key)}
                trailing={selected ? <Check size={14} aria-hidden className="relative shrink-0 text-primary" /> : undefined} />
            </div>
          })}
        </section>
      })}
      <Button variant="ghost" size="sm" onClick={close} className="w-full">Done</Button>
    </div>}
  </Popover>
}
