import { useEffect, useState } from 'react'
import { Bold, FunctionSquare, Italic, Loader2, Type } from 'lucide-react'
import { api, type SheetCellJson, type SheetJson } from '../../data/api'
import { Button } from '../Button'
import { Centered } from '../Centered'
import { InlineError } from '../InlineError'
import { EmptyState } from '../ListScaffold'
import { Segmented } from '../Segmented'
import { Select } from '../forms'
import { asFormula, asLiteral, canBeFormula, cellAt, cellRef, cellText, columnCount, columnLabel, formatOptions, parseEntry, withCell } from './sheetModelEdit'
import { useStructuredEditor } from './structuredEditorState'
import { StructuredLossGate, StructuredSaveControl, confirmStructuredSave } from './structuredEditorChrome'
import type { DocumentEditorProps } from './contentTypes'

type CellAddress = { row: number; col: number }
const transport = { load: api.artifactSheetModel, save: api.saveArtifactSheetModel }
const emphasis = [{ key: 'bold', label: 'Bold', Icon: Bold }, { key: 'italic', label: 'Italic', Icon: Italic }] as const

export function SheetCells({ sheet, selected, editable, reason, onSelect, onEdit }: {
  sheet: SheetJson; selected: CellAddress | null; editable: boolean; reason: string
  onSelect: (address: CellAddress) => void; onEdit: (address: CellAddress, cell: SheetCellJson) => void
}) {
  const columns = Array.from({ length: columnCount(sheet) }, (_, index) => index)
  return <table data-type="body-s" className="border-separate border-spacing-0 overflow-hidden rounded-lg border border-outline/30">
    <caption className="sr-only">{sheet.name} — {sheet.cells.length} rows by {columns.length} columns. Cells holding a formula show the formula, not a calculated result.</caption>
    <thead><tr><th scope="col" className="sticky left-0 z-10 bg-surface-container px-3 py-2"><span className="sr-only">Row</span></th>
      {columns.map(col => <th key={col} scope="col" className="border-b border-outline/30 bg-surface-container/60 px-3 py-2 font-medium text-on-surface-var">{columnLabel(col)}</th>)}
    </tr></thead>
    <tbody>{sheet.cells.map((_, row) => <tr key={row}>
      <th scope="row" className="sticky left-0 z-10 border-r border-outline/30 bg-surface-container px-3 py-2 text-right font-normal text-on-surface-low">{row + 1}</th>
      {columns.map(col => {
        const cell = cellAt(sheet, row, col)
        const active = selected?.row === row && selected.col === col
        const address = { row, col }
        return <td key={col} className="border-b border-r border-outline/20 p-0">
          <input type="text" aria-label={cellRef(sheet, row, col)} value={cellText(cell)} readOnly={!editable} disabled={!editable} title={reason || undefined}
            className={`w-[9rem] px-3 py-2 tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary disabled:opacity-60 ${active ? 'bg-primary/5 ring-1 ring-inset ring-primary/50' : 'bg-transparent'} ${cell.bold ? 'font-semibold' : ''} ${cell.italic ? 'italic' : ''} ${cell.formula ? 'text-primary' : 'text-on-surface'}`}
            style={{ textAlign: (cell.align || 'left') as 'left' | 'center' | 'right' }}
            onFocus={() => onSelect(address)} onChange={event => { if (editable) { onSelect(address); onEdit(address, { ...cell, ...parseEntry(event.currentTarget.value) }) } }} />
        </td>
      })}
    </tr>)}</tbody>
  </table>
}

export function SheetGrid({ slug, title, readOnly, onDirty }: DocumentEditorProps) {
  const editor = useStructuredEditor(slug, 'workbook', transport, readOnly, onDirty)
  const [sheetIndex, setSheetIndex] = useState(0)
  const [selected, setSelected] = useState<CellAddress | null>(null)
  useEffect(() => { setSheetIndex(0); setSelected(null) }, [slug])
  if (editor.slug === slug && editor.loadError) return <div className="p-l"><InlineError icon multiline>Couldn’t read {title}: {editor.loadError}</InlineError></div>
  if (!editor.ready || !editor.model || !editor.baseline) return <Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>
  const sheet = editor.model.sheets[sheetIndex]
  if (!sheet) return <EmptyState icon={FunctionSquare} title="This workbook has no sheets" hint="There is nothing to edit yet. Add a sheet in a spreadsheet app and re-upload, or ask the agent to generate one." />
  const active = selected ? cellAt(sheet, selected.row, selected.col) : null
  const writeCell = (address: CellAddress, cell: SheetCellJson) => editor.edit(model => withCell(model, sheetIndex, address.row, address.col, cell))
  const transformSelected = (transform: (cell: SheetCellJson) => SheetCellJson) => {
    if (selected) editor.edit(model => withCell(model, sheetIndex, selected.row, selected.col, transform(cellAt(model.sheets[sheetIndex], selected.row, selected.col))))
  }
  const conversions = [
    { label: 'Treat as text', title: 'Keep the leading = as a literal label instead of a formula', Icon: Type, available: !!active?.formula, reason: 'Select a formula cell to turn it back into plain text.', transform: asLiteral },
    { label: 'Treat as formula', title: 'Store this cell as a formula the spreadsheet will calculate', Icon: FunctionSquare, available: !!active && canBeFormula(active), reason: 'A formula has to start with “=”. Type one first, then mark it.', transform: asFormula },
  ]
  return <div className="flex h-full min-h-0 flex-col bg-surface">
    {!editor.baseline.loss.lossless && !editor.acknowledged && <StructuredLossGate noun="workbook" loss={editor.baseline.loss} onAcknowledge={editor.acknowledge} />}
    <div className="flex flex-wrap items-center gap-2 border-b border-outline/30 bg-surface-container/30 px-m py-2">
      <span data-type="caption" className="min-w-[5.5rem] rounded-md bg-surface px-2 py-1 font-mono text-on-surface-var">{selected ? cellRef(sheet, selected.row, selected.col) : '—'}</span>
      <div className="flex rounded-lg border border-outline/30 p-0.5">
        {conversions.map(({ label, title, Icon, available, reason, transform }) => <Button key={label} size="xs" variant="ghost" shape="squircle" ariaLabel={label} title={title} disabled={!editor.editable || !available} disabledReason={editor.reason || reason} onClick={() => transformSelected(transform)}><Icon size={14} aria-hidden="true" /></Button>)}
        {emphasis.map(({ key, label, Icon }) => <Button key={key} size="xs" variant="ghost" shape="squircle" ariaLabel={label} ariaPressed={!!active?.[key]} title={`${label} the selected cell`} disabled={!editor.editable || !active} disabledReason={editor.reason || 'Select a cell first.'} onClick={() => transformSelected(cell => ({ ...cell, [key]: !cell[key] }))}><Icon size={14} aria-hidden="true" /></Button>)}
      </div>
      <label data-type="caption" className="flex items-center gap-2 text-on-surface-low">Format
        <Select value={active?.number_format ?? ''} options={formatOptions(active?.number_format ?? '')} disabled={!editor.editable || !active}
          disabledReason={editor.reason || 'Select a cell first, then choose how its value is displayed.'} ariaLabel={`Number format for ${cellRef(sheet, selected?.row ?? 0, selected?.col ?? 0)}`} onChange={number_format => transformSelected(cell => ({ ...cell, number_format }))} />
      </label>
      <span data-type="caption" className="text-on-surface-low">{active?.formula ? 'Formula — saved as written; your spreadsheet calculates it.' : selected ? 'Start a cell with “=” to make it a formula.' : 'Select a cell to format it.'}</span>
      <StructuredSaveControl {...editor} onSave={() => void editor.save(baseline => confirmStructuredSave(title, 'workbook', baseline))} />
    </div>
    {editor.model.sheets.length > 1 && <div className="border-b border-outline/30 px-m py-2"><Segmented size="sm" collapse="scroll" ariaLabel="Sheets" value={String(sheetIndex)} options={editor.model.sheets.map((sheet, index) => ({ key: String(index), label: sheet.name || `Sheet ${index + 1}` }))} onChange={key => { setSheetIndex(Number(key)); setSelected(null) }} /></div>}
    {editor.saveError && <InlineError icon multiline className="mx-m mt-2" onDismiss={editor.clearError}>{editor.saveError}</InlineError>}
    <div className="min-h-0 flex-1 overflow-auto p-m">
      <SheetCells sheet={sheet} selected={selected} editable={editor.editable} reason={editor.reason} onSelect={setSelected} onEdit={writeCell} />
      <p data-type="caption" className="mt-3 text-on-surface-low">Formulas are saved exactly as written — this editor does not calculate them, so a cell shows its formula rather than a result. Charts, pivot tables and conditional formatting are listed above if present; they are not carried through a save.</p>
    </div>
  </div>
}
