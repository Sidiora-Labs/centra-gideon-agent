import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Table, THead, Th, Td } from '../../shared/ui/Table'

export const learningPanelClass = 'flex flex-col gap-m rounded-xl border border-outline-variant/30 bg-surface-container/30 p-l'

export function LearningHeading({ id, icon: Icon, children, suffix }: {
  id: string; icon: LucideIcon; children: ReactNode; suffix?: ReactNode
}) {
  return <header className="flex flex-wrap items-center gap-s border-b border-outline-variant/20 pb-m">
    <Icon size={16} aria-hidden="true" className="text-on-surface-var" />
    <h2 id={id} data-type="title-m" className="text-on-surface">{children}</h2>
    {suffix}
  </header>
}

export type LearningColumn<Row> = {
  name: ReactNode; align?: 'left' | 'right'; render: (row: Row) => ReactNode
}
export function LearningTable<Row>({ caption, rows, columns, rowKey }: {
  caption: string; rows: readonly Row[]; columns: readonly LearningColumn<Row>[]; rowKey: (row: Row) => string
}) {
  return <Table caption={caption} wrapClassName="rounded-lg bg-surface-container">
    <THead><tr>{columns.map((column, index) => <Th key={index} align={column.align}>{column.name}</Th>)}</tr></THead>
    <tbody>{rows.map(row => <tr key={rowKey(row)} className="border-t border-outline-variant/30 align-top">
      {columns.map((column, index) => <Td key={index} align={column.align} className={index === 0 ? 'text-on-surface' : 'text-on-surface-var'}>{column.render(row)}</Td>)}
    </tr>)}</tbody>
  </Table>
}

export function measurement(value: number | null | undefined, digits: number, absent = 'not measured'): string {
  return value == null ? absent : value.toFixed(digits)
}
export function signedMeasurement(value: number | null, digits: number, scale = 1, unit = '', absent = 'not measured'): string {
  if (value === null) return absent
  return [value >= 0 ? '+' : '', (value * scale).toFixed(digits), unit].join('')
}
export function measuredRate(value: number | null, digits = 0, absent = 'not measured'): string {
  return value === null ? absent : `${(value * 100).toFixed(digits)}%`
}
export function groupMeasurements<Row>(rows: readonly Row[], keyOf: (row: Row) => string): Map<string, Row[]> {
  const grouped = new Map<string, Row[]>()
  for (const row of rows) {
    const key = keyOf(row)
    const group = grouped.get(key)
    if (group) group.push(row)
    else grouped.set(key, [row])
  }
  return grouped
}
