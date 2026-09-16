import type { ReactNode, ThHTMLAttributes, TdHTMLAttributes, HTMLAttributes } from 'react'
import { cx } from './cx'

type CellLayout = { align?: 'left' | 'right' | 'center'; pad?: boolean }
const alignment = { left: 'text-left', right: 'text-right', center: 'text-center' }
function cellClasses({ align = 'left', pad = true }: CellLayout, className?: string) {
  return cx('align-middle', alignment[align], pad && 'px-m py-s', className)
}

export function Table({ caption, sized = true, className, wrapClassName, children }: {
  caption: string
  sized?: boolean
  className?: string
  wrapClassName?: string
  children: ReactNode
}) {
  return <div data-table-surface className={cx('isolate overflow-x-auto rounded-lg border border-outline-variant/20', wrapClassName)}>
    <table className={cx('w-full border-collapse', sized && 'text-[0.75rem]', className)}>
      <caption className="sr-only">{caption}</caption>
      {children}
    </table>
  </div>
}

export function THead({ className, ...attributes }: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead {...attributes} className={cx('border-b border-outline-variant/30 bg-surface-container text-on-surface-low', className)} />
}

export function Th({ align, pad, className, children, ...attributes }: ThHTMLAttributes<HTMLTableCellElement> & CellLayout) {
  return <th scope="col" {...attributes} className={cellClasses({ align, pad }, cx('font-medium', className))}>{children}</th>
}

export function Td({ align, pad, className, children, ...attributes }: TdHTMLAttributes<HTMLTableCellElement> & CellLayout) {
  return <td {...attributes} className={cellClasses({ align, pad }, className)}>{children}</td>
}
