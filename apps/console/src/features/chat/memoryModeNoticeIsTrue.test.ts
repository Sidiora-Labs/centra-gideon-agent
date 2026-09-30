import { describe, expect, it } from 'vitest'
import { memoryModeCopy } from './memoryModeCopy'

describe('memory mode notices match stored transcript visibility', () => {
  it('explains memory access and transcript visibility for every mode', () => {
    const persistent = memoryModeCopy('persistent')
    expect(persistent.notice).toContain('memories can be read and updated')
    expect(persistent.notice).toContain('appears in chat history and search')

    const incognito = memoryModeCopy('incognito')
    expect(incognito.notice).toContain('saved memories can be read')
    expect(incognito.notice).toContain('memory writes are disabled')
    expect(incognito.notice).toContain('hidden from chat history and search')
    expect(incognito.notice).toContain('Delete this chat to permanently remove it')
    expect(incognito.notice).not.toContain('saved to your history')

    const temporary = memoryModeCopy('temporary')
    expect(temporary.notice).toContain('memory reads and writes are disabled')
    expect(temporary.notice).toContain('hidden from chat history and search until you delete this chat')
    expect(temporary.notice).not.toContain('forgotten when the session ends')
  })
})
