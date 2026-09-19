import type { KnowledgeItem } from '../../shared/data/api'

export const READING_WORDS_PER_MINUTE = 220

function countWords(content: string): number {
  return content.trim().split(/\s+/u).filter(Boolean).length
}

export function estimateReadingMinutes(
  item: Pick<KnowledgeItem, 'word_count' | 'content'>,
): number | null {
  const reported = Number(item.word_count)
  const words = Number.isFinite(reported) && reported > 0
    ? reported
    : countWords(item.content ?? '')
  if (words <= 0) return null
  return Math.max(1, Math.round(words / READING_WORDS_PER_MINUTE))
}

export function readingTimeLabel(
  item: Pick<KnowledgeItem, 'word_count' | 'content'>,
): string | null {
  const minutes = estimateReadingMinutes(item)
  return minutes == null ? null : `${minutes} min read`
}
