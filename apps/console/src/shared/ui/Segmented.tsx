import { useId, useRef } from 'react'
import { motion } from 'framer-motion'
import { ChevronDown, type LucideIcon } from 'lucide-react'
import { fvs, withWeight } from '../theme/fontWeight'
import { expr, physics, spring, useReducedMotion } from '../theme/motion'
import { menuCursorKeydown, useMenuCursor } from '../data/useMenuCursor'
import { useFieldLabelId } from './forms'
import { MenuRow, Popover } from './Popover'
import { controlMotion, segmentTarget } from './controlState'
import { useSegmentLayout } from './segmentedLayout'

export interface SegOption { key: string; label?: string; tone?: string; icon?: LucideIcon; title?: string }
interface SegmentedProps {
  options: SegOption[]; value: string; onChange: (key: string) => void; iconOnly?: boolean
  ariaLabel?: string; disabled?: boolean; size?: 'md' | 'sm'; collapse?: 'scroll' | 'menu'
}

export function Segmented({ options, value, onChange, iconOnly = false, ariaLabel, disabled = false, size = 'md', collapse }: SegmentedProps) {
  const fieldLabelId = useFieldLabelId()
  const compact = size === 'sm'
  const layout = useSegmentLayout(collapse === 'menu', options, iconOnly, compact)
  const naming = ariaLabel ? { 'aria-label': ariaLabel } : { 'aria-labelledby': fieldLabelId }
  const props = { options, value, onChange, iconOnly, disabled, compact }
  const strip = <SegmentStrip {...props} naming={naming} />
  if (collapse === 'scroll') return <div className="max-w-full overflow-x-auto no-scrollbar">{strip}</div>
  if (collapse !== 'menu') return strip
  return <div ref={layout.container} className="relative min-w-0">
    <div ref={layout.probe} aria-hidden inert className="pointer-events-none invisible absolute -z-10 whitespace-nowrap">
      <SegmentStrip {...props} measuring naming={{}} />
    </div>
    {layout.collapsed ? <SegmentMenu {...props} ariaLabel={ariaLabel} fieldLabelId={fieldLabelId} /> : strip}
  </div>
}

type SharedOptions = { options: SegOption[]; value: string; onChange: (key: string) => void; iconOnly: boolean; disabled: boolean; compact: boolean }
function SegmentStrip({ options, value, onChange, iconOnly, disabled, compact, naming, measuring = false }: SharedOptions & {
  naming: { 'aria-label'?: string; 'aria-labelledby'?: string }; measuring?: boolean
}) {
  const reduced = useReducedMotion()
  const identity = useId()
  const tabs = useRef<HTMLDivElement>(null)
  const activeIndex = Math.max(0, options.findIndex((option) => option.key === value))
  const transition = reduced || expr(1, 0) < 0.4 ? spring.spatialFast : physics.snappy
  return <div ref={tabs} role="tablist" {...naming} aria-disabled={disabled || undefined}
    className={`inline-flex items-center gap-0.5 rounded-xl border border-outline-variant/30 ${compact ? 'bg-surface-container/60 p-0.5' : 'bg-surface-container p-1'} ${disabled ? 'pointer-events-none opacity-50' : ''}`}>
    {options.map((option, index) => {
      const selected = option.key === value
      const Icon = option.icon
      const color = selected ? option.tone ?? 'var(--color-on-primary)' : 'var(--color-on-surface-low)'
      const background = option.tone ? `color-mix(in srgb, ${option.tone} 20%, transparent)` : 'var(--color-primary)'
      const dimensions = iconOnly ? (compact ? 'size-6' : 'size-8') : (compact ? 'h-6 px-2.5' : 'h-8 px-m')
      return <motion.button key={option.key} type="button" role="tab" aria-selected={selected}
        aria-label={iconOnly ? option.label ?? option.title ?? option.key : undefined}
        title={option.title ?? option.label} disabled={disabled || measuring} tabIndex={!measuring && index === activeIndex ? 0 : -1}
        onClick={() => { if (!disabled && !measuring) onChange(option.key) }}
        onKeyDown={(event) => {
          if (disabled || measuring || event.defaultPrevented) return
          const next = segmentTarget(event.key, index, options.length)
          if (next === null) return
          event.preventDefault()
          onChange(options[next].key)
          tabs.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus()
        }}
        {...controlMotion(reduced, disabled || measuring, 0.06)} transition={spring.spatialFast}
        data-type={compact ? 'caption' : 'body-s'}
        className={`relative inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${compact ? 'fw-400' : ''} ${dimensions}`}
        style={selected ? withWeight({ color }, 550) : { color }}>
        {selected && <motion.span aria-hidden layoutId={measuring ? undefined : `seg-${identity}`} transition={transition}
          className="absolute inset-0 rounded-lg shadow-sm" style={{ background }} />}
        <span className="relative z-10 inline-flex items-center gap-1.5">
          {Icon && <Icon size={compact ? 12 : 15} className="shrink-0" />}{!iconOnly && option.label}
        </span>
      </motion.button>
    })}
  </div>
}

function SegmentMenu({ options, value, onChange, iconOnly, disabled, compact, ariaLabel, fieldLabelId }: SharedOptions & {
  ariaLabel?: string; fieldLabelId?: string
}) {
  const active = options.find((option) => option.key === value) ?? options[0]
  const Icon = active?.icon
  const bare = iconOnly && !!Icon
  const name = ariaLabel ?? active?.label ?? active?.key
  return <Popover portal placement="bottom" trigger={(open, toggle) => <button type="button" disabled={disabled}
    onClick={toggle} aria-expanded={open} aria-haspopup="listbox" aria-label={name}
    title={bare ? `${ariaLabel ? `${ariaLabel}: ` : ''}${active?.label ?? active?.key ?? ''}` : undefined}
    data-type={compact ? 'caption' : 'body-s'} style={fvs(550)}
    className={`inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/30 bg-surface-container text-on-surface transition-colors hover:bg-surface-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${disabled ? 'pointer-events-none opacity-50' : ''} ${bare ? (compact ? 'size-6 justify-center' : 'size-8 justify-center') : (compact ? 'h-6 px-2.5' : 'h-8 px-m')}`}>
    {Icon && <Icon size={compact ? 12 : 15} className="shrink-0" />}
    {!bare && <><span className="truncate">{active?.label ?? active?.key}</span><ChevronDown size={compact ? 12 : 14} className="shrink-0 text-on-surface-low" /></>}
  </button>}>
    {(close) => <SegmentChoices options={options} value={value} onChange={onChange} disabled={disabled} close={close}
      ariaLabel={ariaLabel} fieldLabelId={fieldLabelId} />}
  </Popover>
}

function SegmentChoices({ options, value, onChange, disabled, close, ariaLabel, fieldLabelId }: {
  options: SegOption[]; value: string; onChange: (key: string) => void; disabled: boolean; close: () => void
  ariaLabel?: string; fieldLabelId?: string
}) {
  const list = useRef<HTMLDivElement>(null)
  const cursor = useMenuCursor({ containerRef: list, count: options.length, openKey: 'open', initialIndex: Math.max(0, options.findIndex((option) => option.key === value)) })
  return <div ref={list} role="listbox" aria-orientation="vertical" aria-label={ariaLabel} aria-labelledby={ariaLabel ? undefined : fieldLabelId}
    onKeyDown={(event) => { menuCursorKeydown(event.nativeEvent, { move: cursor.move, dismiss: close }) }}>
    {options.map((option, index) => <MenuRow key={option.key} role="option" label={option.label ?? option.key}
      icon={option.icon ? <option.icon size={15} /> : undefined} selected={option.key === value} disabled={disabled}
      tabIndex={cursor.tabIndexFor(index)} onClick={() => { if (!disabled) { onChange(option.key); close() } }} />)}
  </div>
}
