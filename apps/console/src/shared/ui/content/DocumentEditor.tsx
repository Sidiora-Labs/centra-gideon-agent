import { useEffect, useId, useRef, useState } from 'react'
import { AlertTriangle, Bold, Code, FileWarning, Italic, Loader2, Ruler, Save } from 'lucide-react'
import type { DocumentBlock, DocumentLossReport } from '../../data/api'
import { Button } from '../Button'
import { Centered } from '../Centered'
import { InlineError } from '../InlineError'
import { MoreRow } from '../MoreRow'
import { confirm } from '../dialog'
import { applyMark, blockText, isTextBlock, pageOf, selectionHasMark, setBlockText, styleOf, withBlock, withPage, withStyle, type RunMark } from './documentModelEdit'
import { PageSetupControls, ParagraphLayoutControls } from './DocumentLayout'
import { useDocumentEditorState, type DocumentBaseline } from './documentEditorState'
import type { DocumentEditorProps } from './contentTypes'

type TextSelection = { block: number; start: number; end: number }
const formatting = [
  { mark: 'bold', label: 'Bold', Icon: Bold },
  { mark: 'italic', label: 'Italic', Icon: Italic },
  { mark: 'code', label: 'Code', Icon: Code },
] as const
const blockNames: Record<DocumentBlock['kind'], string> = {
  heading: 'Heading', paragraph: 'Paragraph', code: 'Code block', bullets: 'Bulleted list',
  numbered: 'Numbered list', table: 'Table', image: 'Image', pagebreak: 'Page break',
}

function LossList({ loss }: { loss: DocumentLossReport }) {
  const visible = loss.items.slice(0, 12)
  return <div data-type="body-s">
    <p className="text-on-surface">{loss.summary}</p>
    <ul className="mt-2 grid gap-1 text-on-surface-var">{visible.map((item, index) =>
      <li key={`${item.kind}:${item.where}:${index}`}><span className="text-on-surface">{item.kind}</span>{' · '}{item.where}{' — '}{item.detail}</li>,
    )}</ul>
    <MoreRow total={loss.items.length} shown={12} noun="losses" />
  </div>
}

function confirmDocumentSave(title: string, baseline: DocumentBaseline) {
  return confirm({
    title: `Save and re-render “${title}”?`, danger: true, confirmLabel: 'Save and re-render', icon: FileWarning,
    body: <div className="grid gap-3">
      <p data-type="body-s" className="text-on-surface">Saving re-creates the file from the structure below, so the constructs Gideon cannot represent will not be in the saved copy:</p>
      <LossList loss={baseline.loss} />
      <p data-type="body-s" className="text-on-surface-var">Version {baseline.version} is kept — you can restore it from Details › Versions at any time.</p>
    </div>,
  })
}

export function DocumentEditor({ slug, title, readOnly, onDirty }: DocumentEditorProps) {
  const editor = useDocumentEditorState(slug, readOnly, onDirty)
  const [selection, setSelection] = useState<TextSelection | null>(null)
  const [pageExpanded, setPageExpanded] = useState(false)
  const [paragraphExpanded, setParagraphExpanded] = useState<number | null>(null)
  const fields = useRef(new Map<number, HTMLTextAreaElement>())
  const fieldPrefix = useId()
  useEffect(() => {
    setSelection(null)
    setParagraphExpanded(null)
    setPageExpanded(false)
  }, [slug])
  const blockedReason = readOnly
    ? 'This version is read-only — open the current version to edit it.'
    : !editor.acknowledged ? 'Read the formatting notice above, then choose “edit anyway”.' : ''
  const captureSelection = (block: number, field: HTMLTextAreaElement) => {
    const { selectionStart: start, selectionEnd: end } = field
    setSelection(end > start ? { block, start, end } : null)
  }
  const selectedBlock = selection && editor.model?.blocks[selection.block]
  const activeMark = (mark: RunMark) => !!selection && !!selectedBlock && selectionHasMark(selectedBlock, selection.start, selection.end, mark)
  const formatSelection = (mark: RunMark) => {
    if (!selection || !selectedBlock || !editor.editable) return
    const { block, start, end } = selection
    const enable = !activeMark(mark)
    editor.edit(model => withBlock(model, block, applyMark(model.blocks[block], start, end, mark, enable)))
    fields.current.get(block)?.setSelectionRange(start, end)
  }

  if (editor.slug === slug && editor.loadError) return <div className="p-l"><InlineError icon multiline>Couldn’t read {title}: {editor.loadError}</InlineError></div>
  if (!editor.ready || !editor.model || !editor.baseline) return <Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>
  const { model, baseline } = editor
  const preservedBlocks = model.blocks.reduce((count, block) => count + Number(!isTextBlock(block)), 0)

  return <div className="flex h-full min-h-0 flex-col bg-surface" data-document-editor={slug}>
    {!baseline.loss.lossless && !editor.acknowledged && <aside role="alert" className="border-b border-warning/30 bg-warning/5 p-l">
      <div className="flex gap-3">
        <AlertTriangle size={18} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
        <div className="min-w-0 space-y-2">
          <p data-type="body-m" className="font-medium text-on-surface">Editing this document loses formatting</p>
          <p data-type="body-s" className="text-on-surface-var">It contains things this editor’s document model cannot hold. Saving re-creates the file, so they will not be in the saved copy. The version you have now is kept and can be restored from Details › Versions.</p>
          <LossList loss={baseline.loss} />
          <Button size="xs" variant="tonal" onClick={editor.acknowledge}>I understand — edit anyway</Button>
        </div>
      </div>
    </aside>}
    <div className="flex flex-wrap items-center gap-2 border-b border-outline/30 bg-surface-container/30 px-m py-2">
      <div className="flex items-center rounded-lg border border-outline/30 p-0.5">{formatting.map(({ mark, label, Icon }) =>
        <Button key={mark} size="xs" variant="ghost" shape="squircle" ariaLabel={label} ariaPressed={activeMark(mark)}
          title={`${label} the selected text`} disabled={!editor.editable || !selectedBlock}
          disabledReason={blockedReason || `Select text in a paragraph, then ${label.toLowerCase()} it.`} onClick={() => formatSelection(mark)}>
          <Icon size={14} aria-hidden="true" />
        </Button>,
      )}</div>
      <span data-type="caption" className="text-on-surface-low">{selection ? 'Formats the selected text' : 'Select text in a paragraph to format it'}</span>
      <div className="ml-auto flex items-center gap-2">
        {editor.dirty && <span data-type="caption" className="text-on-surface-low">Unsaved changes</span>}
        <Button size="xs" variant="primary" shape="squircle" loading={editor.saving} disabled={!editor.editable || !editor.dirty}
          disabledReason={blockedReason || 'No changes to save yet.'} onClick={() => void editor.save(baseline => confirmDocumentSave(title, baseline))}>
          <Save size={14} aria-hidden="true" /> Save
        </Button>
      </div>
    </div>
    {editor.saveError && <InlineError icon multiline className="mx-m mt-2" onDismiss={editor.clearSaveError}>{editor.saveError}</InlineError>}
    <div className="min-h-0 flex-1 overflow-y-auto p-l">
      <div className="mx-auto grid w-full max-w-[46rem] gap-4">
        <section className="overflow-hidden rounded-xl border border-outline/30">
          <Button size="xs" variant="ghost" className="w-full justify-start" ariaExpanded={pageExpanded} onClick={() => setPageExpanded(value => !value)}>
            <Ruler size={14} aria-hidden="true" /> Page layout
          </Button>
          {pageExpanded && <div className="border-t border-outline/30 p-3"><PageSetupControls page={pageOf(model)} readOnly={!editor.editable} disabledReason={blockedReason} onChange={patch => editor.edit(current => withPage(current, patch))} /></div>}
        </section>
        {model.blocks.map((block, index) => {
          const label = blockNames[block.kind] ?? block.kind
          if (!isTextBlock(block)) return <div key={index} data-type="body-s" className="rounded-lg border border-dashed border-outline/40 bg-surface-container/20 px-3 py-2 text-on-surface-var">{label} — kept exactly as it was parsed. This editor does not change it.</div>
          const fieldId = `${fieldPrefix}-${index}`
          const expanded = paragraphExpanded === index
          return <section key={index} className="rounded-xl border border-outline/30 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <label htmlFor={fieldId} data-type="caption" className="font-medium text-on-surface-var">{label}{block.kind === 'heading' ? ` ${block.level}` : ''}</label>
              <Button size="xs" variant="ghost" shape="squircle" ariaExpanded={expanded} ariaLabel={`Layout for this ${label.toLowerCase()}`} title="Alignment, spacing and indents for this paragraph" onClick={() => setParagraphExpanded(expanded ? null : index)}>
                <Ruler size={13} aria-hidden="true" />
              </Button>
            </div>
            <textarea id={fieldId} rows={block.kind === 'paragraph' ? 3 : 2} data-type="body-m"
              ref={element => { if (element) fields.current.set(index, element); else fields.current.delete(index) }}
              className="w-full resize-y rounded-md bg-surface-container/25 px-3 py-2 text-on-surface outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-60"
              value={blockText(block)} disabled={!editor.editable} readOnly={!editor.editable} title={blockedReason || undefined}
              onSelect={event => captureSelection(index, event.currentTarget)} onMouseUp={event => captureSelection(index, event.currentTarget)} onKeyUp={event => captureSelection(index, event.currentTarget)}
              onChange={event => { const text = event.currentTarget.value; editor.edit(current => withBlock(current, index, setBlockText(current.blocks[index], text))) }} />
            {expanded && <ParagraphLayoutControls block={block} style={styleOf(block)} readOnly={!editor.editable} disabledReason={blockedReason} onChange={patch => editor.edit(current => withStyle(current, index, patch))} />}
          </section>
        })}
        {preservedBlocks > 0 && <p data-type="caption" className="text-on-surface-low">{preservedBlocks} block{preservedBlocks === 1 ? '' : 's'} of this document (tables, images, page breaks) are shown above but not editable here — they are written back unchanged.</p>}
      </div>
    </div>
  </div>
}
