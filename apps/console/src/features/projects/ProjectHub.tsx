import type { ReactNode } from 'react'

export function ProjectHub({ children }: { children: ReactNode }) {
  return (
    <div
      data-testid="project-hub"
      className="grid min-h-0 flex-1 grid-cols-1 gap-px overflow-y-auto bg-outline-variant/20 md:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)] md:overflow-hidden"
    >
      {children}
    </div>
  )
}

export function ProjectHubPane({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <section className="flex min-h-0 flex-col bg-surface">
      <h2 className="shrink-0 px-l pb-s pt-m text-[0.75rem] uppercase tracking-wide text-on-surface-low">{title}</h2>
      <div className="min-h-0 flex-1 px-l pb-l md:overflow-y-auto">{children}</div>
    </section>
  )
}
