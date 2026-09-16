export interface MentionTrigger { query: string; at: number }
export function activeMention(value: string, caret: number): MentionTrigger | null {
  if (caret < 0 || caret > value.length) return null
  let marker = caret - 1
  while (marker >= 0 && !/\s/.test(value[marker]) && value[marker] !== '@') marker--
  if (marker < 0 || value[marker] !== '@' || (marker > 0 && !/\s/.test(value[marker - 1]))) return null
  return { query: value.slice(marker + 1, caret), at: marker }
}

export function activeSlash(value: string, caret: number): { query: string } | null {
  if (value[0] !== '/' || caret < 1 || caret > value.length) return null
  const query = value.slice(1)
  return /^[a-z]*$/i.test(query) ? { query } : null
}

export class PromptRecall {
  private index = -1
  private draft = ''
  reset() { this.index = -1 }
  move(direction: 'up' | 'down', history: readonly string[], value: string, anchor: number, head: number): string | undefined {
    if (!history.length) return undefined
    if (direction === 'up') {
      if (this.index < 0 && (anchor !== 0 || head !== 0 || value.trim())) return undefined
      if (this.index < 0) { this.draft = value; this.index = history.length }
      this.index = Math.max(0, Math.min(history.length - 1, this.index - 1))
      return history[this.index]
    }
    if (this.index < 0 || anchor !== value.length || head !== value.length) return undefined
    this.index++
    if (this.index < history.length) return history[this.index]
    this.reset()
    return this.draft
  }
}

export interface EditorSnapshot { text: string; anchor: number; head: number }
interface EditStep { before: EditorSnapshot; after: EditorSnapshot; group: string; time: number }
export class EditorJournal {
  private past: EditStep[] = []
  private future: EditStep[] = []
  record(before: EditorSnapshot, after: EditorSnapshot, group = '', time = Date.now()) {
    if (before.text === after.text) return
    const previous = this.past.at(-1)
    const joins = group.startsWith('input.type') && previous?.group === group && time - previous.time < 1000
      && previous.after.text === before.text && previous.after.anchor === before.anchor && previous.after.head === before.head
    if (joins && previous) { previous.after = after; previous.time = time }
    else this.past.push({ before, after, group, time })
    if (this.past.length > 100) this.past.shift()
    this.future = []
  }
  undo(): EditorSnapshot | undefined {
    const step = this.past.pop()
    if (!step) return undefined
    this.future.push(step)
    return step.before
  }
  redo(): EditorSnapshot | undefined {
    const step = this.future.pop()
    if (!step) return undefined
    this.past.push(step)
    return step.after
  }
}

export interface TypeaheadCursor { list: 'mention' | 'slash'; index: number }
export function updateTypeaheadCursor(previous: TypeaheadCursor | null, list: TypeaheadCursor['list'], index: number | null) {
  if (index === null) return previous?.list === list ? null : previous
  return previous?.list === list && previous.index === index ? previous : { list, index }
}

export function typeaheadAttributes(prefix: string, cursor: TypeaheadCursor | null): Record<string, string> {
  return cursor ? {
    'aria-controls': `${prefix}-${cursor.list}-list`,
    'aria-activedescendant': `${prefix}-${cursor.list}-opt-${cursor.index}`,
    'aria-haspopup': 'listbox',
  } : {}
}
