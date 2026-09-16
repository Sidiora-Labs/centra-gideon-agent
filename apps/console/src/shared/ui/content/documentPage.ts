export type PageSizeName = '' | 'letter' | 'a4' | 'legal' | 'tabloid'
export const PAGE_SIZE_IN: Record<Exclude<PageSizeName, ''>, [number, number]> = {
  letter: [8.5, 11],
  a4: [210 / 25.4, 297 / 25.4],
  legal: [8.5, 14],
  tabloid: [11, 17],
}
export const PAGE_SIZE_LABEL: Record<PageSizeName, string> = { '': 'Template default', letter: 'Letter', a4: 'A4', legal: 'Legal', tabloid: 'Tabloid' }
export const ORIENTATION_LABEL: Record<string, string> = { '': 'Template default', portrait: 'Portrait', landscape: 'Landscape' }
export const ALIGN_LABEL: Record<string, string> = { '': 'Template default', left: 'Left', center: 'Center', right: 'Right', justify: 'Justify' }
export const POINTS_PER_CM = 72 / 2.54
export function ptToCm(points: number): number { return Math.round(points * 100 / POINTS_PER_CM) / 100 }
export function cmToPt(cm: number): number { return cm * POINTS_PER_CM }
export function pageSizeIn(size: PageSizeName, orientation: string): { width: number; height: number } | null {
  if (!size || !Object.hasOwn(PAGE_SIZE_IN, size)) return null
  const dimensions = PAGE_SIZE_IN[size]
  const [width, height] = orientation === 'landscape' ? [...dimensions].reverse() : dimensions
  return { width, height }
}
export interface PreviewGeometry { aspect: number; inset: { top: number; bottom: number; left: number; right: number } }
export function previewGeometry(size: PageSizeName, orientation: string, margins: { top: number; bottom: number; left: number; right: number }): PreviewGeometry | null {
  const page = pageSizeIn(size, orientation)
  if (!page) return null
  const inset = Object.fromEntries(Object.entries(margins).map(([edge, points]) => {
    const extent = (edge === 'left' || edge === 'right' ? page.width : page.height) * 72
    return [edge, Math.max(0, Math.min(45, points / extent * 100))]
  })) as PreviewGeometry['inset']
  return { aspect: page.width / page.height, inset }
}
