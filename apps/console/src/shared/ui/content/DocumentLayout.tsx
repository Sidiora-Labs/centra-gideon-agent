import type { DocumentBlock, DocumentPageSetup, DocumentParagraphStyle } from '../../data/api'
import { Field, NumberField, Select, TextInput } from '../forms'
import { Toggle } from '../Toggle'
import { ALIGN_LABEL, ORIENTATION_LABEL, PAGE_SIZE_LABEL, cmToPt, previewGeometry, ptToCm, type PageSizeName } from './documentPage'

const optionList = (labels: Record<string, string>) => Object.entries(labels).map(([value, label]) => ({ value, label }))
const marginEdges = ['top', 'bottom', 'left', 'right'] as const
const numericStyles: { key: keyof Pick<DocumentParagraphStyle, 'space_before_pt' | 'space_after_pt' | 'line_spacing' | 'indent_left_pt' | 'indent_right_pt' | 'first_line_indent_pt'>; label: string; name: string; min?: number; step?: number; hint?: string }[] = [
  { key: 'space_before_pt', label: 'Space before (pt)', name: 'Space before, in points', min: 0 },
  { key: 'space_after_pt', label: 'Space after (pt)', name: 'Space after, in points', min: 0 },
  { key: 'line_spacing', label: 'Line spacing', name: 'Line spacing multiple', min: 0, step: .25, hint: '0 = template; 1.5 = 150%.' },
  { key: 'indent_left_pt', label: 'Indent left (pt)', name: 'Left indent, in points', min: 0 },
  { key: 'indent_right_pt', label: 'Indent right (pt)', name: 'Right indent, in points', min: 0 },
  { key: 'first_line_indent_pt', label: 'First line (pt)', name: 'First line indent, in points; negative hangs the first line', hint: 'Negative hangs the first line.' },
]

export function PageGeometryPreview({ page }: { page: DocumentPageSetup }) {
  const geometry = previewGeometry(page.size as PageSizeName, page.orientation, { top: page.margin_top_pt, bottom: page.margin_bottom_pt, left: page.margin_left_pt, right: page.margin_right_pt })
  if (!geometry) return <p data-type="caption" className="text-on-surface-low">Choose a page size to preview the page geometry.</p>
  const insets = Object.fromEntries(Object.entries(geometry.inset).map(([edge, value]) => [edge, `${value}%`]))
  return <figure className="rounded-lg bg-surface-low/40 p-3">
    <div role="img" aria-label={`Approximate ${PAGE_SIZE_LABEL[page.size as PageSizeName] ?? page.size} ${page.orientation || 'portrait'} page with the text area inset by the configured margins`}
      className="relative mx-auto w-full max-w-[13rem] rounded-sm border border-outline/60 bg-surface shadow-sm" style={{ aspectRatio: String(geometry.aspect) }} data-aspect={geometry.aspect.toFixed(4)}>
      <div className="absolute border border-dashed border-primary/40 bg-primary/5" style={insets} />
    </div>
    <figcaption data-type="caption" className="mt-2 text-center text-on-surface-low">Approximate page geometry — proportions and margins only, not a preview of how the text will lay out or where pages will break.</figcaption>
  </figure>
}
function LayoutUnavailable({ reason }: { reason: string }) {
  return <p data-type="body-s" className="text-on-surface-var">{reason || 'This version is read-only — open the current version to change its layout.'}</p>
}

export function PageSetupControls({ page, readOnly, disabledReason, onChange }: { page: DocumentPageSetup; readOnly: boolean; disabledReason: string; onChange: (patch: Partial<DocumentPageSetup>) => void }) {
  if (readOnly) return <div className="space-y-3"><LayoutUnavailable reason={disabledReason} /><PageGeometryPreview page={page} /></div>
  return <div className="grid gap-4">
    <div className="grid grid-cols-2 gap-3">
      <Field label="Page size"><Select value={page.size} onChange={size => onChange({ size })} options={optionList(PAGE_SIZE_LABEL)} ariaLabel="Page size" /></Field>
      <Field label="Orientation"><Select value={page.orientation} onChange={orientation => onChange({ orientation })} options={optionList(ORIENTATION_LABEL)} ariaLabel="Orientation" /></Field>
    </div>
    <Field label="Margins (cm)" hint="0 keeps the template's own margin for that edge.">
      <div className="flex flex-wrap gap-3">{marginEdges.map(edge => {
        const key = `margin_${edge}_pt` as const
        return <label key={edge} className="grid gap-1 text-xs text-on-surface-var"><span className="capitalize">{edge}</span>
          <NumberField value={ptToCm(page[key])} onChange={value => onChange({ [key]: cmToPt(value) })} min={0} step={.1} width="w-20" ariaLabel={`${edge} margin in centimetres`} />
        </label>
      })}</div>
    </Field>
    <div className="grid grid-cols-2 gap-3">{(['header', 'footer'] as const).map(position => {
      const key = `${position}_text` as const
      const label = position === 'header' ? 'Header' : 'Footer'
      return <Field key={position} label={label} hint="Plain text only — one line."><TextInput value={page[key]} onChange={value => onChange({ [key]: value })} size="md" ariaLabel={`${label} text`} /></Field>
    })}</div>
    <div className="flex items-center gap-2 rounded-md border border-outline-variant/40 p-2">
      <Toggle on={page.page_numbers} onChange={page_numbers => onChange({ page_numbers })} size="sm" label="Number the pages" />
      <span className="text-xs text-on-surface-var">Number the pages <span className="text-on-surface-low">— a field in the footer, so it counts per page.</span></span>
    </div>
    <PageGeometryPreview page={page} />
  </div>
}

export function ParagraphLayoutControls({ block, style, readOnly, disabledReason, onChange }: { block: DocumentBlock; style: DocumentParagraphStyle; readOnly: boolean; disabledReason: string; onChange: (patch: Partial<DocumentParagraphStyle>) => void }) {
  if (readOnly) return <div className="mt-2 rounded-lg border border-outline/30 p-3"><LayoutUnavailable reason={disabledReason} /></div>
  return <div data-block-kind={block.kind} className="mt-2 grid grid-cols-2 gap-3 rounded-lg border border-outline/30 bg-surface-container/30 p-3 sm:grid-cols-4">
    <Field label="Align"><Select value={style.align} onChange={align => onChange({ align })} options={optionList(ALIGN_LABEL)} ariaLabel="Paragraph alignment" /></Field>
    {numericStyles.map(field => <Field key={field.key} label={field.label} hint={field.hint}>
      <NumberField value={style[field.key]} onChange={value => onChange({ [field.key]: value })} min={field.min} step={field.step} width="w-20" ariaLabel={field.name} />
    </Field>)}
    <Field label="Keep with next"><Toggle on={style.keep_with_next} onChange={keep_with_next => onChange({ keep_with_next })} size="sm" label="Keep with next paragraph" /></Field>
  </div>
}
