import { type ReactNode } from 'react'
import { AnimatePresence } from 'framer-motion'

export function WorkbenchLayout({ topBar, controls, panel, scroll = true, children }: {
  topBar: ReactNode
  controls?: ReactNode
  panel?: ReactNode
  scroll?: boolean
  children: ReactNode
}) {
  return (
    <div className="flex h-full flex-col">
      {topBar}
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          {controls}
          <div className={`min-w-0 flex-1 ${scroll ? 'overflow-y-auto' : 'flex min-h-0 flex-col'}`}>{children}</div>
        </div>
        <AnimatePresence>{panel}</AnimatePresence>
      </div>
    </div>
  )
}
