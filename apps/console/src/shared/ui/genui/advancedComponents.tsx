import { useEffect, useId, useState, type FormEvent } from 'react'
import { Check, Circle, CircleDot, ExternalLink, Search } from 'lucide-react'
import { Button } from '../Button'
import { Field, TextInput } from '../forms'
import { Surface } from '../Surface'
import { cx } from '../cx'
import { fieldTitle, formFields, formPayload, useComponentAction, valueList, valueText } from './componentState'
import type { GenUiComponentDef, GenUiRenderProps } from './registry'
import { useGenUiElementState, useGenUiWidgetState } from './widgetState'

type UnknownRecord = Record<string, unknown>
type CompareDetail = { label: string; value: string }
type CompareItem = { id: string; label: string; description?: string; details: CompareDetail[] }
type TimelineStatus = 'pending' | 'active' | 'done'
type TimelineItem = { id: string; label: string; description?: string; time?: string; status: TimelineStatus }
type SourceItem = { id: string; label: string; url: string; description?: string }

const asRecord = (value: unknown): UnknownRecord | null =>
  value != null && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : null

function optionalText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function compareItems(value: unknown): CompareItem[] {
  return valueList(value).flatMap(raw => {
    const item = asRecord(raw)
    const id = optionalText(item?.id)
    const label = optionalText(item?.label)
    if (!item || !id || !label) return []
    const details = valueList(item.details).flatMap(rawDetail => {
      const detail = asRecord(rawDetail)
      const detailLabel = optionalText(detail?.label)
      if (!detail || !detailLabel || typeof detail.value !== 'string') return []
      return [{ label: detailLabel, value: detail.value }]
    })
    return [{ id, label, description: optionalText(item.description), details }]
  })
}

function timelineItems(value: unknown): TimelineItem[] {
  return valueList(value).flatMap(raw => {
    const item = asRecord(raw)
    const id = optionalText(item?.id)
    const label = optionalText(item?.label)
    if (!item || !id || !label) return []
    const status = item.status === 'active' || item.status === 'done' ? item.status : 'pending'
    return [{ id, label, description: optionalText(item.description), time: optionalText(item.time), status }]
  })
}

function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null
  } catch {
    return null
  }
}

function sourceItems(value: unknown): SourceItem[] {
  return valueList(value).flatMap(raw => {
    const item = asRecord(raw)
    const id = optionalText(item?.id)
    const label = optionalText(item?.label)
    const url = safeHttpUrl(item?.url)
    if (!item || !id || !label || !url) return []
    return [{ id, label, url, description: optionalText(item.description) }]
  })
}

function SectionHeading({ title }: { title: unknown }) {
  return title == null ? null : <h3 data-type="title-s" className="min-w-0 break-words font-semibold text-on-surface">{valueText(title)}</h3>
}

export function CompareComponent({ args }: GenUiRenderProps) {
  const items = compareItems(args.items)
  const { state, setState } = useGenUiElementState()
  const radioName = useId()
  const filter = state.filter ?? ''
  const normalizedFilter = filter.trim().toLocaleLowerCase()
  const visible = normalizedFilter
    ? items.filter(item => [item.label, item.description, ...item.details.flatMap(detail => [detail.label, detail.value])]
      .some(value => value?.toLocaleLowerCase().includes(normalizedFilter)))
    : items

  useEffect(() => {
    if (state.selected && !items.some(item => item.id === state.selected)) setState({ selected: undefined })
  }, [items, setState, state.selected])

  return <section aria-label={valueText(args.title, 'Compare options')} className="min-w-0 space-y-3">
    <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <SectionHeading title={args.title} />
      <div className="w-full min-w-0 sm:max-w-72">
        <TextInput value={filter} onChange={next => setState({ filter: next })} ariaLabel="Filter comparison options" placeholder="Filter options" size="md" surface="base" leadingIcon={<Search aria-hidden size={15} />} />
      </div>
    </div>
    <fieldset className="grid min-w-0 gap-3 md:grid-cols-2">
      <legend className="sr-only">Choose an option</legend>
      {visible.map(item => {
        const selected = state.selected === item.id
        return <label key={item.id} className="block min-w-0 cursor-pointer">
          <Surface tone={selected ? 'container' : 'low'} radius="lg" className={cx('h-full min-w-0 border p-4 transition-colors', selected ? 'border-primary/60 ring-1 ring-primary/20' : 'border-outline-variant/35 hover:border-outline-variant/70')}>
            <div className="flex min-w-0 items-start gap-3">
              <input type="radio" name={radioName} value={item.id} checked={selected} onChange={() => setState({ selected: item.id })} className="mt-1 size-4 shrink-0 accent-primary" />
              <div className="min-w-0 flex-1">
                <div data-type="label-m" className="break-words font-medium text-on-surface">{item.label}</div>
                {item.description && <p data-type="body-s" className="mt-1 break-words text-on-surface-low">{item.description}</p>}
                {!!item.details.length && <dl className="mt-3 grid min-w-0 gap-x-3 gap-y-2 sm:grid-cols-2">
                  {item.details.map((detail, index) => <div key={`${detail.label}:${index}`} className="min-w-0">
                    <dt data-type="caption" className="break-words text-on-surface-low">{detail.label}</dt>
                    <dd data-type="body-s" className="break-words font-medium text-on-surface">{detail.value}</dd>
                  </div>)}
                </dl>}
              </div>
            </div>
          </Surface>
        </label>
      })}
    </fieldset>
    {!visible.length && <p role="status" data-type="body-s" className="rounded-lg bg-surface-high px-3 py-2 text-on-surface-low">No options match this filter.</p>}
  </section>
}

const timelineTone: Record<TimelineStatus, string> = {
  pending: 'border-outline-variant/60 bg-surface text-on-surface-low',
  active: 'border-primary bg-primary text-on-primary',
  done: 'border-primary/50 bg-primary/10 text-primary',
}

function TimelineMarker({ status }: { status: TimelineStatus }) {
  const Icon = status === 'done' ? Check : status === 'active' ? CircleDot : Circle
  return <span className={cx('relative z-10 grid size-7 shrink-0 place-items-center rounded-full border', timelineTone[status])}><Icon aria-hidden size={14} /></span>
}

export function TimelineComponent({ args }: GenUiRenderProps) {
  const items = timelineItems(args.items)
  return <section aria-label={valueText(args.title, 'Timeline')} className="min-w-0 space-y-3">
    <SectionHeading title={args.title} />
    <ol className="min-w-0">
      {items.map((item, index) => <li key={item.id} aria-current={item.status === 'active' ? 'step' : undefined} className="relative flex min-w-0 gap-3 pb-4 last:pb-0">
        {index < items.length - 1 && <span aria-hidden className="absolute bottom-0 left-[0.8125rem] top-7 w-px bg-outline-variant/40" />}
        <TimelineMarker status={item.status} />
        <div className="min-w-0 flex-1 pt-0.5">
          <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <span data-type="label-m" className="min-w-0 break-words font-medium text-on-surface">{item.label}</span>
            {item.time && <time data-type="caption" className="shrink-0 text-on-surface-low">{item.time}</time>}
          </div>
          {item.description && <p data-type="body-s" className="mt-1 break-words text-on-surface-low">{item.description}</p>}
        </div>
      </li>)}
    </ol>
  </section>
}

export function SourcesComponent({ args }: GenUiRenderProps) {
  const items = sourceItems(args.items)
  return <section aria-label={valueText(args.title, 'Sources')} className="min-w-0 space-y-3">
    <SectionHeading title={args.title} />
    <ul className="grid min-w-0 gap-2 sm:grid-cols-2">
      {items.map(item => <li key={item.id} className="min-w-0">
        <Surface tone="high" radius="lg" className="h-full min-w-0 border border-outline-variant/30 p-3">
          <a href={item.url} target="_blank" rel="noreferrer noopener" className="group flex min-w-0 items-start gap-2 text-primary outline-none focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-primary">
            <span data-type="label-m" className="min-w-0 flex-1 break-words font-medium underline-offset-4 group-hover:underline">{item.label}</span>
            <ExternalLink aria-hidden size={14} className="mt-0.5 shrink-0" />
          </a>
          {item.description && <p data-type="body-s" className="mt-1 break-words text-on-surface-low">{item.description}</p>}
          <p data-type="caption" className="mt-2 break-all text-on-surface-low">{item.url}</p>
        </Surface>
      </li>)}
    </ul>
  </section>
}

function selectedCompareItem(elements: ReturnType<typeof useGenUiWidgetState>['elements'], sourceId: string, selectedId: string): CompareItem | undefined {
  const source = elements?.[sourceId]
  if (!source || source.type !== 'Compare') return undefined
  return compareItems(source.props.items).find(item => item.id === selectedId)
}

function PreviewRows({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload)
  return entries.length ? <dl className="grid min-w-0 gap-x-4 gap-y-2 sm:grid-cols-2">
    {entries.map(([key, value]) => <div key={key} className="min-w-0">
      <dt data-type="caption" className="break-words text-on-surface-low">{fieldTitle(key)}</dt>
      <dd data-type="body-s" className="break-words text-on-surface">{typeof value === 'string' ? value || '—' : JSON.stringify(value)}</dd>
    </div>)}
  </dl> : <p data-type="body-s" className="text-on-surface-low">No additional values.</p>
}

export function ActionPreviewComponent({ args }: GenUiRenderProps) {
  const fields = formFields(args.fields)
  const { state, setState } = useGenUiElementState()
  const widget = useGenUiWidgetState()
  const command = useComponentAction()
  const [reviewing, setReviewing] = useState(false)
  const values = state.fields ?? {}
  const sourceId = optionalText(args.selectionFrom)
  const selectedId = sourceId ? widget.state[sourceId]?.selected : undefined
  const selectedItem = sourceId && selectedId ? selectedCompareItem(widget.elements, sourceId, selectedId) : undefined
  const selectionField = optionalText(args.selectionField) ?? 'selection'
  const basePayload = asRecord(args.payload) ?? {}
  const payload = {
    ...basePayload,
    ...formPayload(fields, values),
    ...(selectedId ? { [selectionField]: selectedId } : {}),
  }
  const label = valueText(args.label, 'Confirm')
  const confirmLabel = valueText(args.confirmLabel, 'Confirm')
  const reviewKey = JSON.stringify({
    action: valueText(args.action),
    label,
    payload,
    fields,
  })

  useEffect(() => setReviewing(false), [reviewKey])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!reviewing) {
      setReviewing(true)
      return
    }
    void command.submit({ action: valueText(args.action), label, payload })
  }

  return <Surface tone="low" radius="xl" className="min-w-0 border border-outline-variant/35 p-4">
    <form onSubmit={submit} className="min-w-0 space-y-4" aria-busy={command.busy || undefined}>
      <div className="min-w-0">
        <h3 data-type="title-s" className="break-words font-semibold text-on-surface">{valueText(args.title)}</h3>
        {args.description != null && <p data-type="body-s" className="mt-1 break-words text-on-surface-low">{valueText(args.description)}</p>}
      </div>
      {!!fields.length && <div className="grid min-w-0 gap-3 sm:grid-cols-2">
        {fields.map(field => <Field key={field} label={fieldTitle(field)}>
          <TextInput name={field} ariaLabel={fieldTitle(field)} value={values[field] ?? ''} onChange={next => setState(previous => ({ ...previous, fields: { ...previous.fields, [field]: next } }))} surface="base" />
        </Field>)}
      </div>}
      {sourceId && <Surface tone="high" radius="lg" className="min-w-0 border border-outline-variant/30 p-3">
        <p data-type="caption" className="text-on-surface-low">Selected option</p>
        {selectedId ? <div className="mt-1 min-w-0">
          <p data-type="label-m" className="break-words font-medium text-on-surface">{selectedItem?.label ?? selectedId}</p>
          {selectedItem?.description && <p data-type="body-s" className="mt-1 break-words text-on-surface-low">{selectedItem.description}</p>}
          {!!selectedItem?.details.length && <dl className="mt-2 grid min-w-0 gap-x-3 gap-y-2 sm:grid-cols-2">
            {selectedItem.details.map((detail, index) => <div key={`${detail.label}:${index}`} className="min-w-0"><dt data-type="caption" className="break-words text-on-surface-low">{detail.label}</dt><dd data-type="body-s" className="break-words text-on-surface">{detail.value}</dd></div>)}
          </dl>}
        </div> : <p data-type="body-s" className="mt-1 text-on-surface-low">Choose an option before continuing.</p>}
      </Surface>}
      {reviewing && <Surface tone="container" radius="lg" className="min-w-0 border border-primary/25 p-3">
        <p data-type="label-m" className="mb-2 font-medium text-on-surface">Review action</p>
        <PreviewRows payload={payload} />
      </Surface>}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Button type="submit" size="sm" loading={command.busy} disabled={!!sourceId && !selectedId}>{reviewing ? confirmLabel : 'Review action'}</Button>
        {reviewing && <Button type="button" size="sm" variant="secondary" disabled={command.busy} onClick={() => setReviewing(false)}>Cancel</Button>}
      </div>
      {command.error && <p role="alert" data-type="caption" className="break-words text-danger">{command.error}</p>}
      {command.status && <p role="status" data-type="caption" className="break-words text-on-surface-low">{command.status}</p>}
    </form>
  </Surface>
}

export const advancedDefinitions: GenUiComponentDef[] = [
  { name: 'Compare', group: 'Data', description: 'Filter and select one grounded option', component: CompareComponent, args: [
    { key: 'title', type: 'string' }, { key: 'items', type: 'any', required: true, note: 'option objects with stable ids' },
  ] },
  { name: 'Timeline', group: 'Data', description: 'Ordered grounded events and statuses', component: TimelineComponent, args: [
    { key: 'title', type: 'string' }, { key: 'items', type: 'any', required: true, note: 'event objects with stable ids' },
  ] },
  { name: 'Sources', group: 'Data', description: 'Grounded HTTP and HTTPS references', component: SourcesComponent, args: [
    { key: 'title', type: 'string' }, { key: 'items', type: 'any', required: true, note: 'source objects with safe URLs' },
  ] },
  { name: 'ActionPreview', group: 'Forms', description: 'Edit, review and explicitly confirm an existing action', component: ActionPreviewComponent, args: [
    { key: 'title', type: 'string', required: true }, { key: 'action', type: 'string', required: true }, { key: 'label', type: 'string', required: true },
    { key: 'description', type: 'string' }, { key: 'payload', type: 'any' }, { key: 'fields', type: 'string[]' },
    { key: 'selectionFrom', type: 'string' }, { key: 'selectionField', type: 'string' }, { key: 'confirmLabel', type: 'string' },
  ] },
]
