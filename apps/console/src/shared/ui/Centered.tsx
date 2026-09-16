import type { ReactNode } from 'react'

export function Centered({ children }: { children: ReactNode }) {
  return <div className="flex h-full items-center justify-center">{children}</div>
}
