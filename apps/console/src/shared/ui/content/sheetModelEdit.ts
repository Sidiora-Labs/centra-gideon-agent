import type { SheetCellJson, SheetJson, SheetModelJson } from '../../data/api'

export function emptyCell(): SheetCellJson {
  return { value: null, formula: '', number_format: '', bold: false, italic: false, font_color: '', fill: '', align: '' }
}
export function columnLabel(index: number): string {
  if (!Number.isInteger(index) || index < 0) return ''
  const letters: string[] = []
  let ordinal = index + 1
  while (ordinal > 0) {
    const digit = (ordinal - 1) % 26
    letters.push(String.fromCharCode(65 + digit))
    ordinal = (ordinal - digit - 1) / 26
  }
  return letters.reverse().join('')
}
export function columnCount(sheet: SheetJson): number {
  let width = 0
  for (const row of sheet.cells) if (row.length > width) width = row.length
  return width
}
export function cellRef(sheet: SheetJson, row: number, col: number): string { return `${sheet.name}!${columnLabel(col)}${row + 1}` }
export function cellText(cell: SheetCellJson): string { return cell.formula || (cell.value === null ? '' : String(cell.value)) }
const formulaText = (text: string) => text.length > 1 && text[0] === '='
export function parseEntry(text: string): Pick<SheetCellJson, 'value' | 'formula'> {
  if (formulaText(text)) return { formula: text, value: null }
  const normalized = text.trim().toLowerCase()
  let value: SheetCellJson['value'] = text
  if (!text.length) value = null
  else if (normalized === 'true' || normalized === 'false') value = normalized === 'true'
  else if (normalized.length && Number.isFinite(Number(text))) value = Number(text)
  return { formula: '', value }
}
export function asLiteral(cell: SheetCellJson): SheetCellJson {
  return cell.formula ? { ...cell, formula: '', value: cell.formula } : cell
}
export function canBeFormula(cell: SheetCellJson): boolean { return !cell.formula && formulaText(cellText(cell)) }
export function asFormula(cell: SheetCellJson): SheetCellJson {
  return canBeFormula(cell) ? { ...cell, value: null, formula: cellText(cell) } : cell
}
export function withCell(model: SheetModelJson, sheetIndex: number, row: number, col: number, next: SheetCellJson): SheetModelJson {
  const sheet = model.sheets[sheetIndex]
  const existing = sheet?.cells[row]
  if (!existing || !Number.isInteger(col) || col < 0) return model
  const cells = sheet.cells.slice()
  cells[row] = Array.from({ length: Math.max(existing.length, col + 1) }, (_, index) => index === col ? next : existing[index] ?? emptyCell())
  const sheets = model.sheets.slice()
  sheets[sheetIndex] = { ...sheet, cells }
  return { ...model, sheets }
}
export function cellAt(sheet: SheetJson, row: number, col: number): SheetCellJson { return sheet.cells[row]?.[col] ?? emptyCell() }
export const NUMBER_FORMATS: { code: string; label: string }[] = [
  { code: '', label: 'Automatic' }, { code: '0', label: 'Whole number (1235)' },
  { code: '#,##0.00', label: 'Number (1,234.50)' }, { code: '0.0%', label: 'Percent (12.5%)' },
  { code: '$#,##0.00', label: 'Currency ($1,234.50)' }, { code: '0.00E+00', label: 'Scientific (1.23E+03)' },
  { code: 'yyyy-mm-dd', label: 'Date (2026-08-26)' }, { code: '@', label: 'Text' },
]
export function formatOptions(current: string): { value: string; label: string }[] {
  const options = NUMBER_FORMATS.map(({ code, label }) => ({ value: code, label }))
  if (current && !options.some(option => option.value === current)) options.push({ value: current, label: `Custom (${current})` })
  return options
}
