import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { chatSessionOwns, releaseClosedChatSession } from '../ChatPage'

describe('closed chat session ownership', () => {
  it('closed chat releases its session before late work settles', async () => {
    const sessionRef = { current: 'session-a' as string | null }
    let settleSnapshot!: () => void
    const snapshotRead = new Promise<void>((resolve) => { settleSnapshot = resolve })
    let adopted = false
    const lateAdoption = snapshotRead.then(() => {
      if (chatSessionOwns(sessionRef, 'session-a')) adopted = true
    })

    releaseClosedChatSession(sessionRef, 'session-a')
    settleSnapshot()
    await lateAdoption

    expect(sessionRef.current).toBeNull()
    expect(adopted).toBe(false)

    const source = readFileSync('src/features/ChatPage.tsx', 'utf8')
    expect(source).toMatch(/alive = false\s+releaseClosedChatSession\(sessionRef, sessionId\)/)
    expect(source).toMatch(/if \(!sessionId \|\| !streaming\) return/)
    expect(source).toMatch(/rememberSpeechOwner\(sessionSpeechStorage\(\), sid, clientTs\)/)
    expect(source).toMatch(/const sid = sessionId \?\? sessionRef\.current/)
  })
})
