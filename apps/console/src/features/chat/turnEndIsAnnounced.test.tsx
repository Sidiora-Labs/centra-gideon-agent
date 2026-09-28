import { describe, expect, it } from 'vitest'
import { claimTurnEndAnnouncement, readTurnOutcome } from './turnOutcome'

describe('terminal turn outcome announcements', () => {
  it('announces each known terminal outcome once for its stream turn', () => {
    const announced = new Set<string>()
    const cursor = { stream_epoch: 'epoch', stream_turn: 4 }
    expect(claimTurnEndAnnouncement(announced, 'session', cursor, readTurnOutcome('complete')))
      .toEqual({ sentence: 'Response complete.', cue: 'turn_complete' })
    expect(claimTurnEndAnnouncement(announced, 'session', cursor, readTurnOutcome('complete')))
      .toBeNull()
    expect(claimTurnEndAnnouncement(announced, 'session', { ...cursor, stream_turn: 5 }, readTurnOutcome('stopped')))
      .toEqual({ sentence: 'Response stopped.', cue: 'turn_complete' })
    expect(claimTurnEndAnnouncement(announced, 'session', { ...cursor, stream_turn: 6 }, readTurnOutcome('error')))
      .toEqual({ sentence: 'Response ended with an error.', cue: 'error' })
  })

  it('treats superseded work as stopped and ignores retryable or unknown events', () => {
    expect(readTurnOutcome(null, true)).toBe('stopped')
    expect(readTurnOutcome('retrying')).toBeNull()
    expect(readTurnOutcome('unknown')).toBeNull()
  })
})
