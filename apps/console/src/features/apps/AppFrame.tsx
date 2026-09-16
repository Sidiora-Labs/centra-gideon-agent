import { useEffect, useRef, useState } from 'react'
import { TopBar } from '../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { SidePanel } from '../../shared/ui/SidePanel'
import { PageTitle } from '../../shared/ui/PageTitle'
import { AppIcon } from './appIcon'
import { ContributedPage } from './ContributedPage'
import type { AppContext } from '../../app/shell/appSdk'

export interface AppHeaderAction {
  id: string
  label: string
  icon?: string
  variant?: 'primary' | 'secondary' | 'ghost'
  onClick: () => void
}

export interface AppPanelSpec {
  title: string
  icon?: string
  render: (el: HTMLElement) => void | (() => void)
}

export interface AppHost {
  setHeaderActions: (actions: AppHeaderAction[]) => void
  openPanel: (spec: AppPanelSpec) => void
  closePanel: () => void
}

export function AppFrame({ app, title, icon, src, mountFunction }: {
  app: AppContext
  title: string
  icon?: string
  src: string
  mountFunction?: string
}) {
  const [actions, setActions] = useState<AppHeaderAction[]>([])
  const [panel, setPanel] = useState<AppPanelSpec | null>(null)
  const hostRef = useRef<AppHost>({
    setHeaderActions: (a) => setActions(a ?? []),
    openPanel: (spec) => setPanel(spec),
    closePanel: () => setPanel(null),
  })

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={
            <PageTitle className="flex items-center gap-s">
              <AppIcon name={icon} size={18} /> <span className="truncate">{title}</span>
            </PageTitle>
          }
          right={actions.length > 0 ? <AppHeaderActions actions={actions} /> : undefined}
        />
      }
      panel={panel && (
        <SidePanel key={panel.title} fillHeight storeKey="app-detail-w"
          title={panel.title} icon={<AppIcon name={panel.icon} size={18} />}
          onClose={() => setPanel(null)}>
          <PanelMount spec={panel} />
        </SidePanel>
      )}
    >
      {
}
      <div className="mx-auto h-full w-full" style={{ maxWidth: 'var(--content-width)' }}>
        <ContributedPage app={app} host={hostRef.current} src={src} mountFunction={mountFunction} />
      </div>
    </WorkbenchLayout>
  )
}

function AppHeaderActions({ actions }: { actions: AppHeaderAction[] }) {
  return (
    <div className="flex items-center gap-s">
      {actions.map((a) => {
        const variant = a.variant ?? 'secondary'
        const cls = variant === 'primary'
          ? 'bg-primary text-on-primary hover:bg-primary-emphasis'
          : variant === 'ghost'
            ? 'bg-transparent text-on-surface hover:bg-surface-high'
            : 'bg-surface-high text-on-surface hover:bg-surface-highest'
        return (
          <button key={a.id} type="button" onClick={a.onClick}
            className={`inline-flex h-9 shrink-0 items-center gap-s rounded-pill px-l text-[0.8125rem] font-[450] transition-colors ${cls}`}>
            {a.icon && <AppIcon name={a.icon} size={15} />}{a.label}
          </button>
        )
      })}
    </div>
  )
}

function PanelMount({ spec }: { spec: AppPanelSpec }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const cleanup = spec.render(ref.current)
    return () => { try { (cleanup as (() => void) | undefined)?.() } catch {   } }
  }, [spec])
  return <div ref={ref} />
}
