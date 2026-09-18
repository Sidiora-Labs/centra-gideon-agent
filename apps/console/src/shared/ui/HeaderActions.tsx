import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { menuCursorKeydown, useMenuCursor } from '../data/useMenuCursor'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, ChevronDown, MoreHorizontal, type LucideIcon } from 'lucide-react'
import { cx } from './cx'
import { spring } from '../theme/motion'
import { fvs, withWeight } from '../theme/fontWeight'
import { Popover, MenuRow } from './Popover'
import { Segmented, type SegOption } from './Segmented'

export type Tier = 'full' | 'text' | 'icon' | 'overflow'
export type Priority = 'primary' | 'default' | 'low'

export interface ChildModes { options: SegOption[]; value: string; onChange: (key: string) => void }

interface ChildReg {
  id: number
  priority: Priority
  canIcon: boolean
  neverOverflow?: boolean
  menu: { label: string; icon?: LucideIcon; hint?: string; danger?: boolean; onSelect?: () => void; modes?: ChildModes }
}

interface ClusterCtx {
  tier: Tier
  visibleIds: Set<number> | null
  register: (reg: ChildReg) => void
  unregister: (id: number) => void
}

const Cluster = createContext<ClusterCtx | null>(null)
const PRIORITY_RANK: Record<Priority, number> = { primary: 0, default: 1, low: 2 }

let _uid = 0
const nextId = () => ++_uid

export function useHeaderTier(): Tier {
  return useContext(Cluster)?.tier ?? 'full'
}

export function useHeaderChild(reg: Omit<ChildReg, 'id'>): { visible: boolean; tier: Tier } {
  const ctx = useContext(Cluster)
  const idRef = useRef<number>(0)
  if (!idRef.current) idRef.current = nextId()
  const id = idRef.current
  const regRef = useRef(reg)
  regRef.current = reg
  useLayoutEffect(() => {
    if (!ctx) return
    ctx.register({ id, ...regRef.current })
    return () => ctx.unregister(id)
  }, [ctx, id, reg.priority, reg.canIcon, reg.menu.label, reg.menu.danger, reg.menu.hint, reg.menu.modes?.value])
  if (!ctx) return { visible: true, tier: 'full' }
  const visible = ctx.tier !== 'overflow' || !ctx.visibleIds || ctx.visibleIds.has(id)
  return { visible, tier: ctx.tier }
}

const TIER_ORDER: Tier[] = ['full', 'text', 'icon', 'overflow']

export const titleFloor = (inner: number): number =>
  Math.round(Math.min(96, Math.max(48, inner * 0.34)))

export function titleReserveFor(
  { hasContent, naturalWidth, inner }: { hasContent: boolean; naturalWidth: number; inner: number },
): number {
  if (!hasContent) return 0
  return Math.min(naturalWidth, titleFloor(inner))
}

export function railCeiling(
  { inner, dots, title }: { inner: number; dots: number; title: number },
): number {
  return Math.max(0, inner - dots - title)
}

export function HeaderActions({ children, className }: { children: ReactNode; className?: string }) {
  const outerRef = useRef<HTMLDivElement>(null)
  const probeFull = useRef<HTMLDivElement>(null)
  const probeText = useRef<HTMLDivElement>(null)
  const probeIcon = useRef<HTMLDivElement>(null)

  const [tier, setTier] = useState<Tier>('full')
  const [visibleIds, setVisibleIds] = useState<Set<number> | null>(null)

  useEffect(() => {
    for (const p of [probeFull, probeText, probeIcon]) p.current?.setAttribute('inert', '')
  }, [])
  const [maxW, setMaxW] = useState<number | null>(null)

  const regs = useRef<Map<number, ChildReg>>(new Map())
  const [regVersion, setRegVersion] = useState(0)
  const register = useCallback((r: ChildReg) => { regs.current.set(r.id, r); setRegVersion((v) => v + 1) }, [])
  const unregister = useCallback((id: number) => { regs.current.delete(id); setRegVersion((v) => v + 1) }, [])

  const tierRef = useRef<Tier>(tier)
  tierRef.current = tier

  useLayoutEffect(() => {
    const outer = outerRef.current
    if (!outer) return
    const HYST = 8
    const GAP_TO_TITLE = 16

    const innerBox = (): number => {
      const header = outer.closest('header')
      if (!header) return outer.clientWidth
      const cs = getComputedStyle(header)
      return header.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0)
    }
    const titleReserve = (inner: number): number => {
      const header = outer.closest('header')
      const left = header?.querySelector<HTMLElement>('[data-header-left]')
      if (!left) return 0
      const hasContent = Array.from(left.children).some((c) => {
        const r = (c as HTMLElement).getBoundingClientRect()
        return r.width > 2 && r.height > 2
      })
      return titleReserveFor({
        hasContent,
        naturalWidth: Math.min(left.scrollWidth, left.clientWidth || left.scrollWidth),
        inner,
      })
    }
    const availableWidth = (): number => {
      const header = outer.closest('header')
      if (!header) return outer.clientWidth
      const inner = innerBox()
      return Math.max(0, inner - titleReserve(inner) - GAP_TO_TITLE)
    }

    const measure = () => {
      const avail = availableWidth()
      const wFull = probeFull.current?.scrollWidth ?? 0
      const wText = probeText.current?.scrollWidth ?? 0
      const wIcon = probeIcon.current?.scrollWidth ?? 0
      const cur = tierRef.current

      const fits = (natural: number, target: Tier) => {
        const rank = TIER_ORDER.indexOf(target)
        const curRank = TIER_ORDER.indexOf(cur)
        const margin = rank < curRank ? HYST : 0
        return natural + margin <= avail
      }

      let next: Tier
      if (fits(wFull, 'full')) next = 'full'
      else if (fits(wText, 'text')) next = 'text'
      else if (fits(wIcon, 'icon')) next = 'icon'
      else next = 'overflow'

      if (next !== 'overflow') {
        if (next !== cur) setTier(next)
        setVisibleIds((prev) => (prev === null ? prev : null))
        setMaxW((prev) => (prev === null ? prev : null))
        return
      }

      const DOTS = 44
      const GAP = 8
      const ordered = [...regs.current.values()]
        .map((r, domOrder) => ({ r, domOrder }))
        .sort((a, b) => PRIORITY_RANK[a.r.priority] - PRIORITY_RANK[b.r.priority] || a.domOrder - b.domOrder)
      const iconEls = probeIcon.current ? Array.from(probeIcon.current.children) as HTMLElement[] : []
      const domIds = [...regs.current.keys()]
      const iconW = (id: number) => {
        const i = domIds.indexOf(id)
        return (iconEls[i]?.offsetWidth ?? 40) + GAP
      }
      const keep = new Set<number>()
      let used = DOTS
      let floorW = 0
      for (const r of regs.current.values()) {
        if (r.neverOverflow) { keep.add(r.id); used += iconW(r.id); floorW += iconW(r.id) }
      }
      for (const { r } of ordered) {
        if (keep.has(r.id)) continue
        if (!r.canIcon) continue
        const w = iconW(r.id)
        if (used + w <= avail) { used += w; keep.add(r.id) }
      }
      if (cur !== 'overflow') setTier('overflow')
      setVisibleIds((prev) => (sameSet(prev, keep) ? prev : keep))
      const inner = innerBox()
      const ceiling = railCeiling({ inner, dots: DOTS, title: titleReserve(inner) })
      const cap = used > avail
        ? Math.min(Math.max(floorW, Math.round(avail - DOTS)), ceiling)
        : null
      setMaxW((prev) => (prev === cap ? prev : cap))
    }

    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(outer)
    const header = outer.closest('header')
    if (header) ro.observe(header)
    const left = header?.querySelector<HTMLElement>('[data-header-left]')
    if (left) ro.observe(left)
    return () => ro.disconnect()
  }, [regVersion])

  const ctx = useMemo<ClusterCtx>(() => ({ tier, visibleIds, register, unregister }), [tier, visibleIds, register, unregister])

  const overflowActions = useMemo(() => {
    if (tier !== 'overflow') return []
    const menuIds = [...regs.current.keys()].filter((id) => !visibleIds || !visibleIds.has(id))
    return menuIds
      .map((id) => regs.current.get(id))
      .filter((r): r is ChildReg => !!r)
      .map((r) => ({ id: r.id, ...r.menu }))
  }, [tier, visibleIds, regVersion])

  return (
    <Cluster.Provider value={ctx}>
      <div ref={outerRef} className={cx('relative min-w-0 flex-1 flex items-center justify-end', className)}>
        {
}
        <div ref={probeFull} aria-hidden className="pointer-events-none absolute right-0 top-0 opacity-0 -z-10 flex items-center gap-s whitespace-nowrap">
          <Cluster.Provider value={{ tier: 'full', visibleIds: null, register: noop, unregister: noop }}>{children}</Cluster.Provider>
        </div>
        <div ref={probeText} aria-hidden className="pointer-events-none absolute right-0 top-0 opacity-0 -z-10 flex items-center gap-s whitespace-nowrap">
          <Cluster.Provider value={{ tier: 'text', visibleIds: null, register: noop, unregister: noop }}>{children}</Cluster.Provider>
        </div>
        <div ref={probeIcon} aria-hidden className="pointer-events-none absolute right-0 top-0 opacity-0 -z-10 flex items-center gap-s whitespace-nowrap">
          <Cluster.Provider value={{ tier: 'icon', visibleIds: null, register: noop, unregister: noop }}>{children}</Cluster.Provider>
        </div>
        {
}
        <div className="min-w-0 flex items-center gap-s">
          <div className="min-w-0 flex items-center gap-s overflow-x-auto no-scrollbar"
            style={maxW != null ? { maxWidth: maxW } : undefined}>
            {children}
          </div>
          {
}
          <AnimatePresence>
            {tier === 'overflow' && overflowActions.length > 0 && (
              <motion.div key="overflow" initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.7 }} transition={spring.spatialFast}>
                <HeaderOverflow actions={overflowActions} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </Cluster.Provider>
  )
}

function noop() {}
function sameSet(a: Set<number> | null, b: Set<number>): boolean {
  if (!a || a.size !== b.size) return false
  for (const x of b) if (!a.has(x)) return false
  return true
}

function HeaderOverflow({ actions }: { actions: { id: number; label: string; icon?: LucideIcon; hint?: string; danger?: boolean; onSelect?: () => void; modes?: ChildModes }[] }) {
  return (
    <Popover align="right" width={220} placement="bottom"
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-label="More actions" title="More actions" aria-expanded={open}
          className={cx('inline-flex items-center justify-center size-10 rounded-pill shrink-0 transition-colors',
            open ? 'bg-surface-high text-on-surface' : 'text-on-surface-var hover:bg-surface-high hover:text-on-surface')}>
          <MoreHorizontal size={20} />
        </button>
      )}>
      {(close) => (
        <div className="flex flex-col gap-0.5">
          {actions.map((a) => (a.modes ? (
            <div key={a.id} role="group" aria-label={a.label} className="flex flex-col gap-0.5">
              <span data-type="caption" className="px-m pt-1 text-on-surface-low">{a.label}</span>
              {a.modes.options.map((o) => (
                <MenuRow key={o.key} role="menuitemradio" selected={o.key === a.modes!.value}
                  icon={o.icon ? <o.icon size={16} /> : undefined} label={o.label ?? o.key} hint={o.title}
                  onClick={() => { a.modes!.onChange(o.key); close() }} />
              ))}
            </div>
          ) : (
            <div key={a.id} className={a.danger ? '[&_button]:text-danger' : ''}>
              <MenuRow icon={a.icon ? <a.icon size={16} /> : undefined} label={a.label} hint={a.hint}
                onClick={() => { a.onSelect?.(); close() }} />
            </div>
          )))}
        </div>
      )}
    </Popover>
  )
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
const variants: Record<Variant, string> = {
  primary: 'bg-primary text-on-primary hover:bg-primary-emphasis',
  secondary: 'bg-surface-high text-on-surface hover:bg-surface-highest',
  ghost: 'bg-transparent text-on-surface hover:bg-surface-high',
  danger: 'bg-danger text-on-danger hover:opacity-90',
}

export function HeaderControl({
  icon: Icon, label, onClick, variant = 'ghost', active, ariaExpanded, disabled, danger,
  priority = 'default', hint, className,
}: {
  icon?: LucideIcon
  label: string
  onClick?: () => void
  variant?: Variant
  active?: boolean
  ariaExpanded?: boolean
  disabled?: boolean
  danger?: boolean
  priority?: Priority
  hint?: string
  className?: string
}) {
  const { visible, tier } = useHeaderChild({
    priority,
    canIcon: !!Icon,
    menu: { label, icon: Icon, hint, danger, onSelect: disabled ? undefined : onClick },
  })
  if (!visible) return null

  const iconOnly = (tier === 'icon' || tier === 'overflow') && !!Icon
  const showLabel = !iconOnly
  const showIcon = !!Icon
  const eff: Variant = danger ? 'danger' : variant
  return (
    <motion.button
      type="button" onClick={onClick} disabled={disabled} title={label}
      aria-label={iconOnly ? label : undefined}
      aria-pressed={ariaExpanded === undefined ? active : undefined}
      aria-expanded={ariaExpanded}
      whileTap={disabled ? undefined : { scale: 0.96 }}
      transition={spring.spatialFast}
      data-type="label-s"
      className={cx(
        'inline-flex items-center justify-center gap-1.5 rounded-pill select-none shrink-0',
        'transition-colors duration-100 disabled:opacity-40 disabled:pointer-events-none',
        iconOnly ? 'size-10' : 'h-10 px-l',
        active ? variants.secondary : variants[eff],
        className,
      )}
      style={fvs(470)}
    >
      {showIcon && <Icon size={16} />}
      {showLabel && <span className="whitespace-nowrap">{label}</span>}
    </motion.button>
  )
}

export function HeaderSegmented({ options, value, onChange, ariaLabel, disabled }: {
  options: SegOption[]
  value: string
  onChange: (k: string) => void
  ariaLabel?: string
  disabled?: boolean
}) {
  const { tier } = useHeaderChild({
    priority: 'primary',
    canIcon: true,
    neverOverflow: true,
    menu: { label: ariaLabel ?? 'Options' },
  })
  const iconOnly = tier === 'icon' || tier === 'overflow'
  return <Segmented options={options} value={value} onChange={onChange} ariaLabel={ariaLabel} disabled={disabled} iconOnly={iconOnly} collapse="menu" />
}

export function HeaderModePill({ options, value, onChange, ariaLabel, disabled }: {
  options: SegOption[]
  value: string
  onChange: (k: string) => void
  ariaLabel?: string
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const closeTimer = useRef<number | undefined>(undefined)
  const changeRef = useRef(onChange)
  changeRef.current = onChange
  const emit = useCallback((key: string) => changeRef.current(key), [])
  const { visible, tier } = useHeaderChild({
    priority: 'primary',
    canIcon: true,
    menu: { label: ariaLabel ?? 'Options', modes: { options, value, onChange: emit } },
  })
  const iconOnly = tier === 'icon' || tier === 'overflow'
  const active = options.find((o) => o.key === value) ?? options[0]
  const { move, restoreFocus, tabIndexFor } = useMenuCursor({
    containerRef: menuRef,
    count: options.length,
    openKey: open ? 'open' : null,
    initialIndex: Math.max(0, options.findIndex((o) => o.key === value)),
    autoFocus: false,
  })

  const measure = useCallback(() => {
    const el = wrapRef.current
    if (el) setRect(el.getBoundingClientRect())
  }, [])
  const suppressFocusOpen = useRef(false)
  const doOpen = useCallback(() => {
    if (disabled || suppressFocusOpen.current) return
    window.clearTimeout(closeTimer.current)
    measure()
    setOpen(true)
  }, [disabled, measure])
  const armClose = useCallback(() => {
    window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setOpen(false), 120)
  }, [])
  const closeNow = useCallback(() => { window.clearTimeout(closeTimer.current); setOpen(false) }, [])
  const dismiss = useCallback(() => {
    suppressFocusOpen.current = true
    closeNow()
    restoreFocus()
    window.setTimeout(() => { suppressFocusOpen.current = false }, 0)
  }, [closeNow, restoreFocus])

  useEffect(() => {
    if (!open) return
    const onScrollResize = () => measure()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { dismiss(); return }
      menuCursorKeydown(e, { move, dismiss })
    }
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (wrapRef.current?.contains(t) || menuRef.current?.contains(t)) return
      closeNow()
    }
    window.addEventListener('scroll', onScrollResize, true)
    window.addEventListener('resize', onScrollResize)
    window.addEventListener('keydown', onKey)
    window.addEventListener('pointerdown', onDown, true)
    return () => {
      window.removeEventListener('scroll', onScrollResize, true)
      window.removeEventListener('resize', onScrollResize)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('pointerdown', onDown, true)
    }
  }, [open, measure, closeNow, move, dismiss])
  useEffect(() => () => window.clearTimeout(closeTimer.current), [])

  if (!active || !visible) return null
  const ActiveIcon = active.icon
  const label = active.label ?? active.key

  return (
    <div ref={wrapRef} className="relative shrink-0"
      onMouseEnter={doOpen} onMouseLeave={armClose}
      onFocus={doOpen}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) armClose() }}>
      {
}
      <button type="button" aria-haspopup="menu" aria-expanded={open} aria-label={`${ariaLabel ?? 'Mode'}: ${label}`}
        title={active.title ?? `${ariaLabel ?? 'Mode'}: ${label}`} disabled={disabled}
        onClick={() => (open ? closeNow() : doOpen())}
        data-type="label-s"
        className={cx(
          'inline-flex items-center justify-center gap-1.5 rounded-pill select-none shrink-0',
          'transition-colors duration-100 disabled:opacity-40 disabled:pointer-events-none',
          iconOnly ? 'size-10' : 'h-10 px-l',
          open ? 'bg-surface-highest text-on-surface' : 'bg-surface-high text-on-surface hover:bg-surface-highest',
        )}
        style={fvs(470)}>
        {ActiveIcon && <ActiveIcon size={16} className="shrink-0" />}
        {!iconOnly && <span className="whitespace-nowrap">{label}</span>}
        {!iconOnly && <ChevronDown size={13} className="shrink-0 -mr-1 text-on-surface-low transition-transform" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />}
      </button>
      {
}
      {createPortal(
        <AnimatePresence>
          {open && !disabled && rect && (
            <motion.div ref={menuRef}
              initial={{ opacity: 0, scale: 0.97, y: -4 }}
              animate={{ opacity: 1, scale: 1, y: 0, transition: spring.spatialFast }}
              exit={{ opacity: 0, scale: 0.98, transition: spring.effects }}
              style={{ position: 'fixed', top: rect.bottom + 4, right: Math.max(8, window.innerWidth - rect.right), transformOrigin: 'top right' }}
              role="menu" aria-label={ariaLabel}
              className="z-[var(--z-menu)]"
              onMouseEnter={doOpen} onMouseLeave={armClose}>
              {
}
              <div className="flex flex-col gap-0.5 rounded-lgi bg-surface-container p-s min-w-[11rem]" style={{ boxShadow: 'var(--shadow-menu)' }}>
                {options.map((o, i) => {
                  const on = o.key === value
                  const OptIcon = o.icon
                  return (
                    <button key={o.key} type="button" role="menuitemradio" aria-checked={on}
                      tabIndex={tabIndexFor(i)}
                      onClick={() => { onChange(o.key); dismiss() }}
                      title={o.title ?? o.label}
                      data-type="body-s"
                      className="flex items-center gap-s w-full rounded-md px-m h-9 text-left transition-colors"
                      style={on
                        ? withWeight({ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }, 550)
                        : { color: 'var(--color-on-surface)' }}
                      onMouseEnter={(e) => { if (!on) e.currentTarget.style.background = 'var(--color-surface-high)' }}
                      onMouseLeave={(e) => { if (!on) e.currentTarget.style.background = 'transparent' }}>
                      {OptIcon && <OptIcon size={15} className="shrink-0" />}
                      <span className="whitespace-nowrap">{o.label ?? o.key}</span>
                      {on && <Check size={14} className="ml-auto shrink-0" />}
                    </button>
                  )
                })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </div>
  )
}
