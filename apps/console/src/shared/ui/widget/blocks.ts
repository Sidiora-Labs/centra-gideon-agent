export interface MdSegment { type: 'md'; content: string }
export interface WidgetSegment { type: 'widget'; title: string; slug?: string; html: string; complete: boolean; kind?: string }
export type ContentSegment = MdSegment | WidgetSegment

function widget(attributes: string, body: string, complete: boolean): WidgetSegment {
  const fields = new Map<string, string>()
  for (const match of attributes.matchAll(/\b(\w+)="([^"]*)"/g)) {
    if (!fields.has(match[1])) fields.set(match[1], match[2])
  }
  return { type: 'widget', title: fields.get('title') || 'Widget', slug: fields.get('slug'), kind: fields.get('kind'), html: body.trim(), complete }
}

export function parseWidgetBlocks(raw: string, streaming = false): ContentSegment[] {
  const segments: ContentSegment[] = []
  const opening = /<widget((?:\s+\w+="[^"]*")*)\s*>/g
  let consumed = 0
  const prose = (end: number) => {
    const content = raw.slice(consumed, end)
    if (content.trim()) segments.push({ type: 'md', content })
  }
  for (const start of raw.matchAll(opening)) {
    if (start.index < consumed) continue
    const bodyStart = start.index + start[0].length
    const close = raw.indexOf('</widget>', bodyStart)
    if (close < 0) {
      if (!streaming) break
      prose(start.index)
      segments.push(widget(start[1], raw.slice(bodyStart), false))
      return segments
    }
    prose(start.index)
    segments.push(widget(start[1], raw.slice(bodyStart, close), true))
    consumed = close + '</widget>'.length
  }
  prose(raw.length)
  return segments
}

export function findGenUiBlock(raw: string): WidgetSegment | null {
  const found = parseWidgetBlocks(raw).find(segment => segment.type === 'widget' && segment.kind === 'genui' && segment.complete)
  return found?.type === 'widget' ? found : null
}

export function widgetlessText(raw: string): string {
  return parseWidgetBlocks(raw).flatMap(segment => segment.type === 'md' && segment.content.trim() ? [segment.content.trim()] : []).join('\n\n')
}
