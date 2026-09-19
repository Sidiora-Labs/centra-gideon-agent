import { describe, expect, it } from 'vitest'
import { estimateReadingMinutes, readingTimeLabel } from './readingTime'

describe('reading time', () => {
  it('uses the indexed word count when it is available', () => {
    expect(estimateReadingMinutes({ word_count: 440 })).toBe(2)
    expect(readingTimeLabel({ word_count: 440 })).toBe('2 min read')
  })

  it('keeps short readable items at one minute', () => {
    expect(estimateReadingMinutes({ word_count: 12 })).toBe(1)
  })

  it('counts the body when older rows have no indexed word count', () => {
    expect(estimateReadingMinutes({ content: Array.from({ length: 440 }, () => 'word').join(' ') })).toBe(2)
  })

  it('does not advertise a reading time for an empty item', () => {
    expect(estimateReadingMinutes({ content: '  \n ' })).toBeNull()
    expect(readingTimeLabel({})).toBeNull()
  })
})
