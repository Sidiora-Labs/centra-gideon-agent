import { Decoration, EditorView, ViewPlugin, WidgetType, type DecorationSet } from '@codemirror/view'
import { syntaxTree } from '@codemirror/language'
import type { EditorState, Range } from '@codemirror/state'

const markerNames = new Set(['HeaderMark', 'EmphasisMark', 'StrongEmphasisMark', 'CodeMark', 'QuoteMark', 'ListMark', 'LinkMark', 'StrikethroughMark'])
const formats: Record<string, string> = {
  StrongEmphasis: 'strong', Emphasis: 'emphasis', Strikethrough: 'strike', InlineCode: 'code', Blockquote: 'quote',
  ...Object.fromEntries(Array.from({ length: 6 }, (_, index) => [`ATXHeading${index + 1}`, `heading-${index + 1}`])),
}
const decorations = new Map(Object.entries(formats).map(([node, suffix]) => [node, Decoration.mark({ class: `cm-gideon-${suffix}` })]))
const hidden = Decoration.replace({})
class ListBullet extends WidgetType {
  eq(other: WidgetType) { return other instanceof ListBullet }
  toDOM() {
    const element = document.createElement('span')
    element.className = 'cm-gideon-bullet'
    element.textContent = '•\u00a0'
    return element
  }
}
const bullet = Decoration.replace({ widget: new ListBullet() })

export function markdownDecorations(state: EditorState, visible: readonly { from: number; to: number }[]): DecorationSet {
  const active = state.selection.ranges.map(range => ({
    from: state.doc.lineAt(range.from).number, to: state.doc.lineAt(range.to).number,
  }))
  const seen = new Set<string>()
  const ranges: Range<Decoration>[] = []
  const tree = syntaxTree(state)
  for (const window of visible) tree.iterate({
    from: window.from, to: window.to,
    enter(node) {
      if (node.from === node.to) return
      const identity = `${node.name}:${node.from}:${node.to}`
      if (seen.has(identity)) return
      seen.add(identity)
      const format = decorations.get(node.name)
      if (format) ranges.push(format.range(node.from, node.to))
      if (!markerNames.has(node.name)) return
      const line = state.doc.lineAt(node.from).number
      if (active.some(range => line >= range.from && line <= range.to)) return
      if (node.name !== 'ListMark') ranges.push(hidden.range(node.from, node.to))
      else if (/^[-*+]$/.test(state.sliceDoc(node.from, node.to).trim())) ranges.push(bullet.range(node.from, node.to))
    },
  })
  return Decoration.set(ranges, true)
}

export const liveMarkdown = ViewPlugin.define(view => {
  let tree = syntaxTree(view.state)
  return {
    decorations: markdownDecorations(view.state, view.visibleRanges),
    update(update) {
      const nextTree = syntaxTree(update.state)
      if (update.docChanged || update.selectionSet || update.viewportChanged || nextTree !== tree) {
        this.decorations = markdownDecorations(update.state, update.view.visibleRanges)
      }
      tree = nextTree
    },
  }
}, { decorations: value => value.decorations })

const headingSizes = ['1.65em', '1.4em', '1.2em', '1.08em', '1em', '1em']
export const liveMarkdownTheme = EditorView.theme({
  '&': { backgroundColor: 'transparent', color: 'var(--color-on-surface)', fontSize: '17px' },
  '.cm-content': { padding: '4px 4px 0', fontFamily: 'inherit', lineHeight: '1.55', caretColor: 'var(--color-primary)' },
  '.cm-line': { padding: '0' },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { fontFamily: 'inherit', lineHeight: '1.55' },
  '.cm-placeholder': { color: 'var(--color-on-surface-low)' },
  '.cm-cursor': { borderLeftColor: 'var(--color-primary)' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'color-mix(in srgb, var(--color-primary) 28%, transparent)' },
  ...Object.fromEntries(headingSizes.map((size, index) => [`.cm-gideon-heading-${index + 1}`, {
    fontSize: size, fontWeight: index < 2 ? '700' : '650', fontVariationSettings: `"wght" ${Math.max(620, 680 - index * 20)}`,
    lineHeight: '1.3', color: index === 5 ? 'var(--color-on-surface-var)' : 'var(--color-on-surface)',
  }])),
  '.cm-gideon-strong': { fontWeight: '700', fontVariationSettings: '"wght" 700', color: 'var(--color-on-surface)' },
  '.cm-gideon-emphasis': { fontStyle: 'italic' },
  '.cm-gideon-strike': { textDecoration: 'line-through', opacity: '0.7' },
  '.cm-gideon-code': { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.9em', color: 'var(--color-primary-emphasis, var(--color-primary))', background: 'var(--color-surface-high)', borderRadius: '5px', padding: '0.05em 0.3em' },
  '.cm-gideon-quote': { color: 'var(--color-on-surface-var)', fontStyle: 'italic', borderLeft: '2px solid var(--color-outline-variant)', paddingLeft: '0.6em' },
  '.cm-gideon-bullet': { color: 'var(--color-primary)' },
})
