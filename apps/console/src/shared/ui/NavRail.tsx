import { useEffect, useRef, useState } from 'react'
import { withWeight } from '../theme/fontWeight'
import { ChevronDown, Search, type LucideIcon } from 'lucide-react'
import { motion } from 'framer-motion'
import { cx } from './cx'
import { fvs } from '../theme/fontWeight'
import { GideonMark } from './GideonMark'
import { usePersonality } from '../../app/shell/personality'
import { spring } from '../theme/motion'
import { captureFocus, FocusScope } from './focusNavigation'
import './navRail.css'

export interface NavItem {
  id: string
  label: string
  icon: LucideIcon
  badge?: string
  badgeLabel?: string
  section?: string
  pinBottom?: boolean
}

export interface NavDisclosureControl {
  expanded: boolean
  moreCount: number
  onToggle: () => void
}

const W_KEY = 'nav-width-v2'
const W_MIGRATION_KEY = 'nav-width-v2-default-migrated'
const MIN_W = 172
const MAX_W = 380
const COLLAPSED_W = 64
const OVERLAY_W = 264
const DEFAULT_W = 248

export function NavRail({
  items, activeId, onSelect, onSearch, collapsed, overlay = false, overlayOpen = false, onScrimClick, disclosure,
}: {
  items: NavItem[]
  activeId: string
  onSelect: (id: string) => void
  onSearch: () => void
  collapsed: boolean
  disclosure?: NavDisclosureControl
  overlay?: boolean
  overlayOpen?: boolean
  onScrimClick?: () => void
}) {
  const { wordmarkLabel } = usePersonality()
  const [width, setWidth] = useState(() => {
    const v = Number(localStorage.getItem(W_KEY))
    if (v === 196 && localStorage.getItem(W_MIGRATION_KEY) !== '1') return DEFAULT_W
    return v >= MIN_W && v <= MAX_W ? v : DEFAULT_W
  })
  const dragging = useRef(false)
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!overlay || !overlayOpen || !overlayRef.current) return
    return new FocusScope(captureFocus()).attach(overlayRef.current)
  }, [overlay, overlayOpen])

  useEffect(() => {
    if (collapsed) return
    localStorage.setItem(W_KEY, String(width))
    localStorage.setItem(W_MIGRATION_KEY, '1')
  }, [width, collapsed])

  useEffect(() => {
    if (collapsed) return
    const onMove = (e: MouseEvent) => { if (dragging.current) setWidth(Math.max(MIN_W, Math.min(MAX_W, e.clientX))) }
    const onUp = () => { if (dragging.current) { dragging.current = false; document.body.style.cursor = '' } }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
  }, [collapsed])

  const w = collapsed ? COLLAPSED_W : width
  const compact = collapsed && !overlay
  let lastSection: string | undefined

  const topItems = items.filter((i) => !i.pinBottom)
  const pinnedItems = items.filter((i) => i.pinBottom)

  const rowCls = (tone: string) => cx(
    'gideon-nav-row group relative flex items-center gap-s w-full text-left transition-colors duration-100',
    compact ? 'gideon-nav-row-collapsed justify-center px-0' : 'px-s',
    tone,
  )

  const renderItem = (item: NavItem, withSection: boolean) => {
    const showSection = withSection && !compact && item.section && item.section !== lastSection
    if (withSection) lastSection = item.section
    const active = item.id === activeId || activeId.startsWith(`${item.id}/`)
    const Icon = item.icon
    const badgeHint = item.badge ? (item.badgeLabel ?? item.badge) : undefined
    return (
      <div key={item.id} className="gideon-nav-item">
        {showSection && (
          <div data-type="body-s" className="gideon-nav-section"
            style={fvs(600)}>
            {item.section}
          </div>
        )}
        <motion.button
          type="button" onClick={() => onSelect(item.id)} whileTap={{ scale: 0.98 }} transition={spring.spatialFast}
          title={compact ? (badgeHint ? `${item.label}, ${badgeHint}` : item.label) : badgeHint}
          aria-label={badgeHint ? `${item.label}, ${badgeHint}` : item.label}
          aria-current={active ? 'page' : undefined}
          data-type="body-m"
          className={rowCls(active ? 'gideon-nav-row-active' : 'text-on-surface-var')}
          style={withWeight({ height: 32 }, active ? 470 : 400)}>
          {/* Shared active surface slides from
              the previously-active item to this one (layoutId), instead of each
              item toggling its own background. Sits behind the icon/label. */}
          {active && (
            <motion.span
              layoutId="nav-active-pill"
              transition={spring.spatialDefault}
              className="gideon-nav-active absolute inset-0"
            />
          )}
          <span className="gideon-nav-icon relative z-10 shrink-0 inline-flex">
            <Icon size={18} strokeWidth={2} />
            {
}
            {compact && item.badge && (
              <span className="absolute -right-1 -top-1 size-2 rounded-pill ring-2 ring-surface" style={{ background: 'var(--color-primary)' }} />
            )}
          </span>
          {!compact && <span className="relative z-10 flex-1 truncate">{item.label}</span>}
          {!compact && item.badge && (
            <span data-type="caption" className="gideon-nav-badge relative z-10 inline-flex h-5 items-center">
              {item.badge}
            </span>
          )}
        </motion.button>
      </div>
    )
  }

  const showFull = !compact
  const railBody = (
    <nav data-tour="rail" className={cx('gideon-nav flex h-full flex-col gap-1 overflow-y-auto overflow-x-hidden', !showFull && 'gideon-nav-collapsed')}
      style={{ width: overlay ? OVERLAY_W : w, background: 'var(--color-rail)' }}>
      { }
      <div className={cx('gideon-nav-brand flex items-center', !showFull && 'justify-center')}>
        { }
        <span className="gideon-nav-brand-mark"><GideonMark size={30} /></span>
        {showFull && <span className="gideon-nav-wordmark" style={fvs(650)}>{wordmarkLabel}</span>}
      </div>

      <button type="button" className={rowCls('gideon-nav-search')} onClick={onSearch}
        aria-label="Search" title={showFull ? undefined : 'Search'}>
        <Search size={18} aria-hidden="true" />
        {showFull && <span className="gideon-nav-search-label">Search</span>}
      </button>

      {
}
      {topItems.map((item) => renderItem(item, true))}

      {
}
      {disclosure && disclosure.moreCount > 0 && (
        <motion.button
          type="button" onClick={disclosure.onToggle} whileTap={{ scale: 0.98 }} transition={spring.spatialFast}
          aria-expanded={disclosure.expanded}
          aria-label={disclosure.expanded
            ? `Show fewer, hide ${disclosure.moreCount} surface${disclosure.moreCount === 1 ? '' : 's'}`
            : `Everything, show ${disclosure.moreCount} more surface${disclosure.moreCount === 1 ? '' : 's'}`}
          title={disclosure.expanded
            ? `Hide the ${disclosure.moreCount} surface${disclosure.moreCount === 1 ? '' : 's'} you have not opened yet`
            : `Show all ${disclosure.moreCount} remaining surface${disclosure.moreCount === 1 ? '' : 's'}`}
          data-type="body-m"
          className={rowCls('gideon-nav-disclosure text-on-surface-low')}
          style={withWeight({ height: 32 }, 400)}>
          <span className="relative z-10 shrink-0 inline-flex">
            <ChevronDown size={18} strokeWidth={2}
              className={cx('transition-transform', disclosure.expanded && 'rotate-180')} />
          </span>
          {showFull && (
            <span className="relative z-10 flex-1 truncate">
              {disclosure.expanded ? 'Show fewer' : 'Everything'}
            </span>
          )}
          {showFull && !disclosure.expanded && (
            <span data-type="caption" className="gideon-nav-count relative z-10 tabular-nums">+{disclosure.moreCount}</span>
          )}
        </motion.button>
      )}

      {
}
      <div className="gideon-nav-utilities mt-auto">
        {showFull && pinnedItems.length > 0 && <div className="gideon-nav-section">Account</div>}
        {pinnedItems.map((item) => renderItem(item, false))}
      </div>
    </nav>
  )

  if (overlay) {
    return (
      <>
        {
}
        <motion.div
          className="fixed inset-0 z-[calc(var(--z-modal)-1)] bg-black/40"
          initial={false}
          animate={{ opacity: overlayOpen ? 1 : 0 }}
          transition={spring.effects}
          style={{ pointerEvents: overlayOpen ? 'auto' : 'none' }}
          onClick={onScrimClick} aria-hidden />
        {
}
        <motion.div
          ref={overlayRef}
          className="fixed left-0 top-0 z-[var(--z-modal)] h-full shadow-2xl"
          initial={false}
          animate={{ x: overlayOpen ? 0 : '-100%' }}
          transition={spring.spatialDefault}
          role="dialog" aria-label="Navigation" aria-hidden={!overlayOpen}
          inert={!overlayOpen}
          style={{ width: OVERLAY_W }}>
          {railBody}
        </motion.div>
      </>
    )
  }

  return (
    <div className="relative h-full shrink-0" style={{ width: w }}>
      {railBody}
      { }
      {!collapsed && (
        <div role="separator" aria-orientation="vertical" tabIndex={0}
          aria-label="Resize navigation — arrow keys to resize"
          aria-valuenow={Math.round(w)} aria-valuemin={MIN_W} aria-valuemax={MAX_W}
          onMouseDown={() => { dragging.current = true; document.body.style.cursor = 'col-resize' }}
          onKeyDown={(e) => {
            const STEP = 16
            const next = e.key === 'ArrowLeft' ? w - STEP
              : e.key === 'ArrowRight' ? w + STEP
              : e.key === 'Home' ? MIN_W
              : e.key === 'End' ? MAX_W
              : null
            if (next == null) return
            e.preventDefault()
            setWidth(Math.max(MIN_W, Math.min(MAX_W, next)))
          }}
        className="absolute right-0 top-0 z-10 h-full w-1 cursor-col-resize outline-none transition-colors hover:bg-primary/30 focus-visible:bg-primary/60 hit-24-x" />
      )}
    </div>
  )
}
