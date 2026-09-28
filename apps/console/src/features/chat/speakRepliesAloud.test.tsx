// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { claimCompletedReplySpeech, forgetSpeechOwner, rememberSpeechOwner } from './speakRepliesAloud'

describe('completed-reply speech ownership', () => {
  beforeEach(() => sessionStorage.clear())

  it('claims only the completed reply owned by this tab and stable user turn', () => {
    rememberSpeechOwner(sessionStorage, 'session-a', 'user-ts-a')

    expect(claimCompletedReplySpeech(sessionStorage, 'session-a', 'other-tab-ts', 'assistant-ts-a', 'complete')).toBe(false)
    expect(claimCompletedReplySpeech(sessionStorage, 'session-a', 'user-ts-a', 'assistant-ts-a', 'complete')).toBe(true)
  })

  it('does not replay the same assistant message after reload or duplicate completion', () => {
    rememberSpeechOwner(sessionStorage, 'session-a', 'user-ts-a')
    expect(claimCompletedReplySpeech(sessionStorage, 'session-a', 'user-ts-a', 'assistant-ts-a', 'complete')).toBe(true)

    rememberSpeechOwner(sessionStorage, 'session-a', 'user-ts-a')
    expect(claimCompletedReplySpeech(sessionStorage, 'session-a', 'user-ts-a', 'assistant-ts-a', 'complete')).toBe(false)
  })

  it.each(['stopped', 'error'] as const)('never claims a %s turn', (outcome) => {
    rememberSpeechOwner(sessionStorage, 'session-a', 'user-ts-a')
    expect(claimCompletedReplySpeech(sessionStorage, 'session-a', 'user-ts-a', 'assistant-ts-a', outcome)).toBe(false)
    expect(claimCompletedReplySpeech(sessionStorage, 'session-a', 'user-ts-a', 'assistant-ts-a', 'complete')).toBe(false)
  })

  it('clears a cancelled owner without affecting another submitted turn', () => {
    rememberSpeechOwner(sessionStorage, 'session-a', 'user-ts-a')
    forgetSpeechOwner(sessionStorage, 'session-a', 'user-ts-a')
    rememberSpeechOwner(sessionStorage, 'session-a', 'user-ts-b')
    expect(claimCompletedReplySpeech(sessionStorage, 'session-a', 'user-ts-a', 'assistant-ts-a', 'complete')).toBe(false)
    expect(claimCompletedReplySpeech(sessionStorage, 'session-a', 'user-ts-b', 'assistant-ts-b', 'complete')).toBe(true)
  })
})
