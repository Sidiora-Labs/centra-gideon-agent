export const TAIL_WINDOW_WORDS = 6
export type HandsFreeAction = 'accumulate' | 'submit' | 'clear' | 'ignore'
export interface HandsFreeStep { buffer: string; action: HandsFreeAction }

type Phrases = { confirmation: readonly string[]; exit: readonly string[] }
const tokenize = (text: string) => Array.from(text.toLowerCase().matchAll(/[a-z0-9]+(?:'[a-z]+)?/g), match => match[0])
const separator = /[\s,.;:!?-]/

function findTrigger(tokens: readonly string[], phrases: readonly string[], windowSize: number): string[] | undefined {
  for (const phrase of phrases) {
    if (typeof phrase !== 'string') continue
    const sought = tokenize(phrase)
    if (!sought.length) continue
    const span = Math.trunc(Math.max(windowSize, sought.length))
    const first = Number.isNaN(span) ? 0 : Math.max(0, tokens.length - span)
    const last = tokens.length - sought.length
    for (let start = first; start <= last; start++) {
      let offset = 0
      while (offset < sought.length && tokens[start + offset] === sought[offset]) offset++
      if (offset === sought.length) return sought
    }
  }
}

function removeSuffix(text: string, trigger: readonly string[] | undefined): string {
  if (!trigger) return text.trim()
  const lower = text.toLowerCase()
  let cursor = text.length
  const skipSeparators = () => { while (cursor > 0 && separator.test(text[cursor - 1])) cursor-- }
  for (let index = trigger.length - 1; index >= 0; index--) {
    skipSeparators()
    const word = trigger[index]
    if (lower.slice(cursor - word.length, cursor) !== word) return text.trim()
    cursor -= word.length
  }
  skipSeparators()
  return text.slice(0, cursor).trim()
}

export function isConfirmation(text: string, phrases: readonly string[], tailWords = TAIL_WINDOW_WORDS): boolean {
  return findTrigger(tokenize(text), phrases, tailWords) !== undefined
}

export function isExit(text: string, phrases: readonly string[], tailWords = TAIL_WINDOW_WORDS): boolean {
  return findTrigger(tokenize(text), phrases, tailWords) !== undefined
}

export function stripTrailingPhrase(text: string, phrases: readonly string[]): string {
  return removeSuffix(text, findTrigger(tokenize(text), phrases, TAIL_WINDOW_WORDS))
}

export function accumulateTranscript(buffer: string, chunk: string, phrases: Phrases): HandsFreeStep {
  const text = (chunk ?? '').trim()
  if (!text) return { action: 'ignore', buffer }
  const tokens = tokenize(text)
  if (findTrigger(tokens, phrases.exit, TAIL_WINDOW_WORDS)) return { action: 'clear', buffer: '' }
  const confirmation = findTrigger(tokens, phrases.confirmation, TAIL_WINDOW_WORDS)
  const addition = removeSuffix(text, confirmation)
  const next = buffer ? `${buffer} ${addition}`.trim() : addition
  const action: HandsFreeAction = confirmation ? (next ? 'submit' : 'clear') : 'accumulate'
  return { action, buffer: next }
}
