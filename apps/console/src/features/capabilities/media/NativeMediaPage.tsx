import type { ReactNode } from 'react'
import { TopBar } from '../../../shared/ui/TopBar'
import { PageTitle } from '../../../shared/ui/PageTitle'

export default function NativeMediaPage({ title, actions, children, width = 'wide' }: {
  title: string
  actions?: ReactNode
  children: ReactNode
  width?: 'content' | 'wide'
}) {
  return <section aria-label={title} className="flex h-full min-h-0 flex-col text-on-surface">
    <TopBar left={<PageTitle>{title}</PageTitle>} right={actions && <div className="flex items-center gap-m text-primary">{actions}</div>} />
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
      <div
        className="mx-auto w-full space-y-l px-l py-l sm:px-2xl sm:py-2xl [&_h2]:text-on-surface [&_h3]:text-on-surface [&_p]:text-on-surface-var [&_a]:text-primary [&_a]:underline-offset-4 [&_a:hover]:underline [&_[role=alert]]:rounded-md [&_[role=alert]]:bg-danger/10 [&_[role=alert]]:px-m [&_[role=alert]]:py-s [&_[role=alert]]:text-danger [&_[role=status]]:text-on-surface-low [&_fieldset]:min-w-0 [&_fieldset]:space-y-m [&_fieldset]:rounded-lg [&_fieldset]:bg-surface-container [&_fieldset]:p-l [&_ol]:space-y-s [&_ol>li]:rounded-lg [&_ol>li]:bg-surface-container [&_ol>li]:p-l [&_label]:block [&_label]:text-on-surface-var [&_legend]:mb-s [&_legend]:text-on-surface-var [&_button:not([class])]:min-h-10 [&_button:not([class])]:rounded-pill [&_button:not([class])]:bg-surface-high [&_button:not([class])]:px-l [&_button:not([class])]:text-on-surface [&_button:not([class])]:transition-colors [&_button:not([class])]:hover:bg-surface-highest [&_button:not([class])]:disabled:opacity-40 [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:mt-1 [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:min-h-10 [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:w-full [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:rounded-md [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:border [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:border-outline-variant/30 [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:bg-surface-container [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:px-m [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:outline-none [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:focus:ring-2 [&_input:not([type=checkbox]):not([type=color]):not([type=file])]:focus:ring-primary [&_select]:mt-1 [&_select]:min-h-10 [&_select]:w-full [&_select]:rounded-md [&_select]:border [&_select]:border-outline-variant/30 [&_select]:bg-surface-container [&_select]:px-m [&_textarea]:mt-1 [&_textarea]:min-h-28 [&_textarea]:w-full [&_textarea]:resize-y [&_textarea]:rounded-md [&_textarea]:border [&_textarea]:border-outline-variant/30 [&_textarea]:bg-surface-container [&_textarea]:px-m [&_textarea]:py-s [&_textarea]:outline-none [&_textarea]:focus:ring-2 [&_textarea]:focus:ring-primary"
        style={width === 'content' ? { maxWidth: 'var(--content-width)' } : undefined}
      >
        {children}
      </div>
    </div>
  </section>
}
