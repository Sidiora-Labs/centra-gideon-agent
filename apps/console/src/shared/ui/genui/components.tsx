import { useState, type FormEvent, type ReactNode } from 'react'
import { Surface } from '../Surface'
import { Button } from '../Button'
import { Meter } from '../Meter'
import { cx } from '../cx'
import { defineComponent, type GenUiComponentDef, type GenUiRenderProps } from './registry'
import { chartSeries, fieldTitle, formFields, formPayload, progressValue, useComponentAction, valueList, valueText } from './componentState'

const spacing: Record<string, string> = { s: 'gap-s', m: 'gap-m', l: 'gap-l' }
const tones: Record<string, string> = {
  info: 'border-primary/30 bg-primary-container text-on-primary-container',
  ok: 'border-primary/25 bg-primary/10 text-on-surface',
  warn: 'border-warn/30 bg-warn/5 text-warn', danger: 'border-danger/30 bg-danger/5 text-danger',
  neutral: 'border-outline-variant/40 bg-surface-high text-on-surface-var',
}
const tone = (value: unknown) => tones[valueText(value)] ?? tones.neutral

function StackLayout({ args, children }: GenUiRenderProps) {
  const orientation = valueText(args.direction) === 'row' ? 'flex-row flex-wrap items-start' : 'flex-col'
  return <div className={cx('flex min-w-0', orientation, spacing[valueText(args.gap)] ?? spacing.m)}>{children.body}</div>
}

function CardLayout({ args, children }: GenUiRenderProps) {
  return <Surface tone="low" radius="xl" className="overflow-hidden border border-outline-variant/35">
    {args.title != null && <div data-type="label-m" className="border-b border-outline-variant/25 px-4 py-3 font-medium text-on-surface">{valueText(args.title)}</div>}
    <div className="flex flex-col gap-s p-4">{children.body}</div>
  </Surface>
}

function Metric({ args }: GenUiRenderProps) {
  const delta = typeof args.delta === 'number' && Number.isFinite(args.delta) ? args.delta : undefined
  return <Surface tone="high" radius="lg" className="border-l-2 border-primary/50 px-4 py-3">
    <dl>
      <dt data-type="body-s" className="text-on-surface-low">{valueText(args.label)}</dt>
      <dd className="mt-1 flex flex-wrap items-baseline gap-2">
        <span data-type="title-l" className="font-semibold tabular-nums text-on-surface">{valueText(args.value)}</span>
        {delta !== undefined && <span data-type="label-s" className={cx('tabular-nums', delta < 0 ? 'text-danger' : 'text-primary')}>
          {delta < 0 ? '' : '+'}{delta}%
        </span>}
      </dd>
    </dl>
  </Surface>
}

function DataGrid({ args }: GenUiRenderProps) {
  const columns = valueList(args.columns).map(column => valueText(column))
  const rows = valueList(args.rows).map(valueList)
  return <div className="overflow-x-auto rounded-lg border border-outline-variant/35">
    <table data-type="body-s" className="w-full border-collapse text-left">
      {!!columns.length && <thead className="bg-surface-high text-on-surface-var"><tr>{columns.map((column, index) =>
        <th key={index} scope="col" className="border-b border-outline-variant/40 px-3 py-2 font-medium">{column}</th>)}</tr></thead>}
      <tbody>{rows.map((row, index) => <tr key={index} className="even:bg-surface-high/30">
        {row.map((value, column) => <td key={column} className="border-b border-outline-variant/20 px-3 py-2 text-on-surface">{valueText(value)}</td>)}
      </tr>)}</tbody>
    </table>
  </div>
}

function BulletItems({ args }: GenUiRenderProps) {
  return <ul className="list-disc space-y-2 pl-5 marker:text-primary/60">{valueList(args.items).map((item, index) =>
    <li key={index} data-type="body-m" className="pl-1 leading-relaxed text-on-surface">{valueText(item)}</li>)}</ul>
}

function Bars({ args }: GenUiRenderProps) {
  const series = chartSeries(args.data, args.labels)
  return <figure role="img" aria-label={`Bar chart: ${series.map((point, index) => `${point.label ?? index + 1}: ${point.value}`).join(', ')}`}>
    <div aria-hidden className="flex h-40 items-stretch gap-s border-b border-outline-variant/40 px-1">
      {series.map((point, index) => <div key={index} className="flex min-w-0 flex-1 flex-col justify-end gap-1">
        <div className="relative min-h-0 flex-1">
          <div title={String(point.value)} className="absolute inset-x-0 bottom-0 rounded-t-sm bg-primary" style={{ height: `${point.height}%` }} />
        </div>
        {point.label != null && <span data-type="caption" className="truncate pb-1 text-center text-on-surface-low">{point.label}</span>}
      </div>)}
    </div>
  </figure>
}

function NoteBand({ args }: GenUiRenderProps) {
  return <div role="note" data-type="body-s" className={cx('rounded-lg border-l-2 px-3 py-2', tone(args.tone))}>{valueText(args.text)}</div>
}

function StatusTag({ args }: GenUiRenderProps) {
  return <span data-type="caption" className={cx('inline-flex w-fit items-center rounded-md border px-2 py-0.5 font-medium', tone(args.tone))}>{valueText(args.text)}</span>
}

function Completion({ args }: GenUiRenderProps) {
  const label = valueText(args.label, 'Progress')
  return <div className="flex flex-col gap-1">
    {args.label != null && <span data-type="caption" className="text-on-surface-low">{label}</span>}
    <Meter label={label} pct={progressValue(args.value)} />
  </div>
}

function ActionFailure({ message }: { message: string }) {
  return message ? <p role="alert" data-type="caption" className="text-danger">{message}</p> : null
}

function ActionTrigger({ args }: GenUiRenderProps) {
  const command = useComponentAction()
  const label = valueText(args.label, 'Submit')
  return <div className="flex flex-col items-start gap-1">
    <Button variant={valueText(args.tone) === 'danger' ? 'danger' : 'primary'} size="sm" loading={command.busy}
      onClick={() => { void command.submit({ action: valueText(args.action), label, payload: undefined }) }}>{label}</Button>
    <ActionFailure message={command.error} />
  </div>
}

function ActionForm({ args }: GenUiRenderProps) {
  const fields = formFields(args.fields)
  const [values, setValues] = useState(() => new Map<string, string>())
  const command = useComponentAction()
  const label = valueText(args.submit, 'Submit')
  const submit = (event: FormEvent) => {
    event.preventDefault()
    void command.submit({ action: valueText(args.action), label, payload: formPayload(fields, Object.fromEntries(values)) })
  }
  return <form onSubmit={submit} className="flex flex-col gap-3" aria-busy={command.busy || undefined}>
    {args.title != null && <div data-type="label-m" className="font-medium text-on-surface">{valueText(args.title)}</div>}
    <div className="grid gap-3 sm:grid-cols-2">{fields.map((field, index) => <label key={`${field}:${index}`} className="flex min-w-0 flex-col gap-1">
      <span data-type="caption" className="text-on-surface-low">{fieldTitle(field)}</span>
      <input type="text" name={field} value={values.get(field) ?? ''} data-type="body-s"
        onChange={event => { const value = event.currentTarget.value; setValues(previous => new Map(previous).set(field, value)) }}
        className="w-full rounded-lg border border-outline-variant/50 bg-surface px-3 py-2 text-on-surface outline-none focus-visible:border-primary focus-visible:ring-1 focus-visible:ring-primary/25" />
    </label>)}</div>
    <Button type="submit" variant="primary" size="sm" className="w-fit" loading={command.busy}>{label}</Button>
    <ActionFailure message={command.error} />
  </form>
}

const coreDefinitions: GenUiComponentDef[] = [
  { name: 'Stack', group: 'Layout', description: 'Vertical/horizontal stack of children', component: StackLayout, args: [
    { key: 'body', type: 'refs', required: true, note: 'child line ids, in order' },
    { key: 'gap', type: 'string', note: 's | m | l' }, { key: 'direction', type: 'string', note: 'column (default) | row' },
  ] },
  { name: 'Card', group: 'Layout', description: 'Titled card wrapping children', component: CardLayout, args: [
    { key: 'body', type: 'refs', required: true, note: 'child line ids' }, { key: 'title', type: 'string' },
  ] },
  { name: 'StatTile', group: 'Data', description: 'One metric with optional % delta', component: Metric, args: [
    { key: 'label', type: 'string', required: true }, { key: 'value', type: 'string', required: true }, { key: 'delta', type: 'number', note: 'signed percent change' },
  ] },
  { name: 'Table', group: 'Data', description: 'Header row + body rows', component: DataGrid, args: [
    { key: 'columns', type: 'string[]', required: true }, { key: 'rows', type: 'rows', required: true, note: 'array of row arrays' },
  ] },
  { name: 'List', group: 'Data', description: 'Bulleted list of strings', component: BulletItems, args: [{ key: 'items', type: 'string[]', required: true }] },
  { name: 'Bar', group: 'Charts', description: 'Bar chart of one numeric series', component: Bars, args: [
    { key: 'data', type: 'number[]', required: true }, { key: 'labels', type: 'string[]', note: 'per-bar labels' },
  ] },
  { name: 'Callout', group: 'Feedback', description: 'Tinted note band', component: NoteBand, args: [
    { key: 'text', type: 'string', required: true }, { key: 'tone', type: 'string', note: 'info | ok | warn | danger | neutral' },
  ] },
  { name: 'Badge', group: 'Feedback', description: 'Small status chip', component: StatusTag, args: [
    { key: 'text', type: 'string', required: true }, { key: 'tone', type: 'string' },
  ] },
  { name: 'ProgressBar', group: 'Feedback', description: 'Determinate 0..100 progress bar', component: Completion, args: [
    { key: 'value', type: 'number', required: true }, { key: 'label', type: 'string' },
  ] },
  { name: 'Button', group: 'Forms', description: 'Action button — click sends its label as the turn', component: ActionTrigger, args: [
    { key: 'label', type: 'string', required: true, note: 'visible text AND the message the transcript shows' },
    { key: 'action', type: 'string', required: true, note: 'the action name the agent/run receives' }, { key: 'tone', type: 'string', note: 'primary (default) | danger' },
  ] },
  { name: 'Form', group: 'Forms', description: 'Named text fields + one submit action', component: ActionForm, args: [
    { key: 'fields', type: 'string[]', required: true, note: 'field names; values are sent as {name: value}' }, { key: 'action', type: 'string', required: true },
    { key: 'submit', type: 'string', note: 'submit button label (default "Submit")' }, { key: 'title', type: 'string' },
  ] },
]
let installed = false
export function registerCoreGenUiComponents(): void {
  if (installed) return
  for (const definition of coreDefinitions) defineComponent(definition)
  installed = true
}
export type { ReactNode }
