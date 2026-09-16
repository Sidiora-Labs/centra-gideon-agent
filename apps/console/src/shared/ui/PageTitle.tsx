import { cx } from './cx'

export function PageTitle({ children, className }: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <h1 data-type="title-l" className={cx('text-on-surface', className)}>
      {children}
    </h1>
  )
}
