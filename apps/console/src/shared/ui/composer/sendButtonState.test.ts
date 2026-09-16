import { describe, it, expect } from 'vitest'
import { resolveSendButton, sendButtonIsActive, type SendButtonInputs } from './sendButtonState'


const base: SendButtonInputs = {
  processing: false, streaming: false, canSend: false, canQueue: false, justSent: false,
}

describe('resolveSendButton', () => {
  it('idle + empty draft → disabled send (smoke: cannot send a blank message)', () => {
    expect(resolveSendButton(base)).toBe('send-disabled')
    expect(sendButtonIsActive('send-disabled')).toBe(false)
  })

  it('idle + ready draft → live send (smoke: full send path is armed)', () => {
    expect(resolveSendButton({ ...base, canSend: true })).toBe('send')
    expect(sendButtonIsActive('send')).toBe(true)
  })

  it('just after send → transient sent bloom, inert (smoke: send→ confirmation)', () => {
    expect(resolveSendButton({ ...base, justSent: true })).toBe('sent')
    expect(resolveSendButton({ ...base, canSend: true, justSent: true })).toBe('sent')
    expect(sendButtonIsActive('sent')).toBe(false)
  })

  it('streaming + no draft → stop (smoke: stop mid-stream)', () => {
    expect(resolveSendButton({ ...base, streaming: true })).toBe('stop')
    expect(resolveSendButton({ ...base, streaming: true, canSend: true })).toBe('stop')
    expect(sendButtonIsActive('stop')).toBe(true)
  })

  it('streaming + queue-able draft → steer into the running turn', () => {
    expect(resolveSendButton({ ...base, streaming: true, canSend: true, canQueue: true })).toBe('steer')
    expect(resolveSendButton({ ...base, streaming: true, canQueue: true })).toBe('stop')
    expect(sendButtonIsActive('steer')).toBe(true)
  })

  it('processing outranks everything → inert spinner (smoke: one-shot pre-send pass)', () => {
    expect(resolveSendButton({ ...base, processing: true })).toBe('processing')
    expect(resolveSendButton({
      processing: true, streaming: true, canSend: true, canQueue: true, justSent: true,
    })).toBe('processing')
    expect(sendButtonIsActive('processing')).toBe(false)
  })
})
