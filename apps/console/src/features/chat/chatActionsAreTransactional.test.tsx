import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { acceptedActionSnapshot } from './turnOutcome'

const source = readFileSync(join(process.cwd(), 'src/features/ChatPage.tsx'), 'utf8')

describe('chat actions adopt only an accepted server snapshot', () => {
  it('returns the authoritative detail after acceptance', () => {
    const snapshot = { key: 'session', running: true, messages: [{ role: 'user', content: 'Edited prompt' }] }
    expect(acceptedActionSnapshot({ ok: true, snapshot })).toBe(snapshot)
  })

  it('keeps caller state untouched when the server refuses the action', () => {
    const editStart = source.indexOf('async function editResend(')
    const editEnd = source.indexOf('async function rewindTo(', editStart)
    const editBody = source.slice(editStart, editEnd)
    const accepted = editBody.indexOf('await api.editResend(')
    const adopted = editBody.indexOf('adoptAcceptedActionSnapshot(')
    const closed = editBody.indexOf('setEditingTurn(null)')
    const catchBody = editBody.slice(editBody.indexOf('catch (error)'))
    expect(accepted).toBeGreaterThan(-1)
    expect(adopted).toBeGreaterThan(accepted)
    expect(closed).toBeGreaterThan(adopted)
    expect(catchBody).toContain('releaseActionSnapshot(generation)')
    expect(catchBody).toContain('setMicError(')
    expect(catchBody).not.toMatch(/setTurns\(|setInput\(|setEditingTurn\(/)

    const regenerateStart = source.indexOf('async function regenerate()')
    const regenerateEnd = source.indexOf('async function switchVariant(', regenerateStart)
    const regenerateBody = source.slice(regenerateStart, regenerateEnd)
    const request = regenerateBody.indexOf('await api.regenerate(s)')
    const snapshot = regenerateBody.indexOf('adoptAcceptedActionSnapshot(')
    expect(snapshot).toBeGreaterThan(request)
    expect(regenerateBody).not.toMatch(/setTurns\(/)

    expect(() => acceptedActionSnapshot({ ok: false, snapshot: null })).toThrow(
      'The server did not accept this chat change.',
    )
  })
})
