import { useEffect, useState, type ReactNode } from 'react'
import { Sun, Moon, Monitor } from 'lucide-react'
import { IconButton } from './IconButton'
import { useMode } from '../../app/shell/theme'

function useRightPanelOpen(): boolean {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const root = document.documentElement
    const read = () => setOpen((Number(root.style.getPropertyValue('--rightpanel-open')) || 0) > 0)
    read()
    const mo = new MutationObserver(read)
    mo.observe(root, { attributes: true, attributeFilter: ['style'] })
    return () => mo.disconnect()
  }, [])
  return open
}

const TITLE_TRUNCATES = [
  '[&_div]:min-w-0',
  '[&_[data-type]]:min-w-0 [&_[data-type]]:truncate [&_[data-type]]:pr-s',
].join(' ')

export function TopBar({ left, right, keepCornerPadding = false, contentAligned = false, contentWidth }: {
  left?: ReactNode; right?: ReactNode
  keepCornerPadding?: boolean
  contentAligned?: boolean
  contentWidth?: number
}) {
  const panelOpen = useRightPanelOpen() && !keepCornerPadding
  if (contentAligned || contentWidth) {
    const gutter = `calc((100% - ${contentWidth ? `${contentWidth}px` : 'var(--content-width)'}) / 2)`
    const cornerL = 'calc(var(--shell-corner-l, 56px) + var(--spacing-m, 12px))'
    const cornerR = panelOpen ? 'var(--spacing-l, 16px)' : 'calc(var(--shell-corner-r, 140px) + var(--spacing-m, 12px))'
    return (
      <header className="gideon-topbar flex h-14 shrink-0 items-center gap-m"
        style={{ paddingInlineStart: `max(${cornerL}, ${gutter})`, paddingInlineEnd: `max(${cornerR}, ${gutter})` }}>
        {
}
        <div className={`flex min-w-0 flex-1 items-center gap-s ps-l ${TITLE_TRUNCATES}`} data-header-left>{left}</div>
        <div className="flex min-w-0 items-center gap-s pe-l" data-header-right>{right}</div>
      </header>
    )
  }
  return (
    <header className="gideon-topbar flex items-center justify-between gap-m h-14 shrink-0"
      style={{
        paddingInlineStart: 'calc(var(--shell-corner-l, 56px) + var(--spacing-m, 12px))',
        paddingInlineEnd: panelOpen ? 'var(--spacing-l, 16px)' : 'calc(var(--shell-corner-r, 140px) + var(--spacing-m, 12px))',
      }}>
      {
}
      <div className={`flex items-center gap-s min-w-0 flex-1 ${TITLE_TRUNCATES}`} data-header-left>{left}</div>
      <div className="flex min-w-0 items-center gap-s" data-header-right>{right}</div>
    </header>
  )
}

export function ThemeControl() {
  const { preference, setPreference } = useMode()
  const next = preference === 'dark' ? 'light' : preference === 'light' ? 'auto' : 'dark'
  const icon = preference === 'dark' ? Moon : preference === 'light' ? Sun : Monitor
  const label = preference === 'auto' ? 'Theme: follow system' : preference === 'dark' ? 'Theme: dark' : 'Theme: light'
  return <IconButton icon={icon} label={`${label} — switch to ${next === 'auto' ? 'system' : next}`} size={36} onClick={() => setPreference(next)} />
}
