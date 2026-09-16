import { describe, it, expect, vi, beforeEach } from 'vitest'

const playCue = vi.fn()
vi.mock('../../shared/theme/soundCues', () => ({
  playCue: (...args: unknown[]) => playCue(...args),
  CUES: { turn_complete: {}, approval_needed: {}, error: {}, coin_blip: {}, terminal_bell: {} },
  CUE_POINTS: ['turn_complete', 'approval_needed', 'error'],
}))

import { installPushCuePlayback } from './pushCuePlayback'
import { PUSH_CUE_MESSAGE } from './pushPolicy'

beforeEach(() => {
  playCue.mockClear()
  Object.defineProperty(navigator, 'serviceWorker', {
    value: new EventTarget(),
    configurable: true,
  })
})

function post(data: unknown): void {
  navigator.serviceWorker.dispatchEvent(new MessageEvent('message', { data }))
}

describe('installPushCuePlayback', () => {
  it('plays a delivered voice through the gated playCue', () => {
    const off = installPushCuePlayback()
    post({ type: PUSH_CUE_MESSAGE, cue: 'coin_blip' })
    expect(playCue).toHaveBeenCalledWith('turn_complete', 'coin_blip')
    off()
  })

  it('uses a voice that IS a cue point as its own anchor', () => {
    const off = installPushCuePlayback()
    post({ type: PUSH_CUE_MESSAGE, cue: 'error' })
    expect(playCue).toHaveBeenCalledWith('error', 'error')
    off()
  })

  it('ignores an unknown voice and any unrelated message', () => {
    const off = installPushCuePlayback()
    post({ type: PUSH_CUE_MESSAGE, cue: 'ka-ching' })
    post({ type: 'something-else', cue: 'coin_blip' })
    post({ cue: 'coin_blip' })
    post(null)
    expect(playCue).not.toHaveBeenCalled()
    off()
  })

  it('the disposer detaches the listener', () => {
    const off = installPushCuePlayback()
    off()
    post({ type: PUSH_CUE_MESSAGE, cue: 'coin_blip' })
    expect(playCue).not.toHaveBeenCalled()
  })
})
