import { StrictMode } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { cropViewStyle, drawFrame, SHARE_MAX_EDGE } from './displayCapture'
import { accumulateTranscript, isConfirmation, stripTrailingPhrase } from './duplex'
import { MediaOwnership, MediaWriteQueue, frameDimensions, recordedAudio } from './mediaOwnership'
import { HANDS_FREE_SEGMENT_MS, MicCaptureSession } from './micCaptureSession'
import { resolveSendButton, sendButtonIsActive, type SendButtonInputs } from './sendButtonState'
import { useMicRecorder } from './useMicRecorder'

const phrases = { confirmation: ['send it', 'go ahead'], exit: ['cancel', 'never mind'] }

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

describe('capture ownership', () => {
  it('ignores the result of a permission request cancelled before completion', async () => {
    const ownership = new MediaOwnership()
    const ticket = ownership.begin()
    const completion = Promise.resolve().then(() => ownership.finish(ticket))
    ownership.cancel()
    expect(await completion).toBe(false)
    expect(ownership.busy).toBe(false)
  })

  it('a previous operation cannot release its replacement', () => {
    const ownership = new MediaOwnership()
    const previous = ownership.begin()
    const current = ownership.begin()
    expect(ownership.owns(previous)).toBe(false)
    expect(ownership.finish(previous)).toBe(false)
    expect(ownership.busy).toBe(true)
    expect(ownership.finish(current)).toBe(true)
    expect(ownership.busy).toBe(false)
    expect(ownership.finish(current)).toBe(false)
  })

  it('keeps cancelled and completed generations invalid across restarts', () => {
    const ownership = new MediaOwnership()
    const stale: number[] = []
    for (let index = 0; index < 10; index++) {
      const ticket = ownership.begin()
      expect(stale.every(prior => !ownership.owns(prior))).toBe(true)
      stale.push(ticket)
      if (index % 2) ownership.cancel()
      else ownership.finish(ticket)
    }
    expect(new Set(stale).size).toBe(10)
  })
})

describe('ordered screen mutations', () => {
  it('finishes staging before stopping, then starts the next share', async () => {
    const writes = new MediaWriteQueue()
    const history: string[] = []
    const stage = writes.run(async () => {
      history.push('stage started')
      await Promise.resolve()
      history.push('stage complete')
      return new File(['screen'], 'frame.png', { type: 'image/png' })
    })
    const stop = writes.run(async () => { history.push('stop') })
    const restart = writes.run(async () => { history.push('start') })
    expect((await stage).name).toBe('frame.png')
    await Promise.all([stop, restart])
    expect(history).toEqual(['stage started', 'stage complete', 'stop', 'start'])
  })

  it('reports a rejected write and still performs queued teardown', async () => {
    const writes = new MediaWriteQueue()
    const error = new Error('capture no longer available')
    const failed = writes.run(() => Promise.reject(error))
    const outcome = failed.catch(value => value)
    const stop = writes.run(() => Promise.resolve('stopped'))
    expect(await outcome).toBe(error)
    expect(await stop).toBe('stopped')
  })
})

describe('frame and audio payloads', () => {
  it.each([
    [3840, 2160, SHARE_MAX_EDGE, 1568, 882],
    [2160, 3840, SHARE_MAX_EDGE, 882, 1568],
    [800, 600, SHARE_MAX_EDGE, 800, 600],
    [3840, 2160, 0, 3840, 2160],
    [0.1, 0.1, 1, 1, 1],
  ])('sizes %s × %s to budget %s', (width, height, limit, expectedWidth, expectedHeight) => {
    expect(frameDimensions(width, height, limit)).toEqual({ width: expectedWidth, height: expectedHeight })
  })

  it.each([[0, 1080], [1920, 0], [-1, 10], [NaN, 10], [10, Infinity]])('rejects unreadable dimensions %s × %s', (w, h) => {
    expect(frameDimensions(w, h)).toBeNull()
  })

  it('declines a real video element until frame metadata exists', () => {
    const video = document.createElement('video')
    expect(drawFrame(video)).toBeNull()
  })

  it('keeps malformed preview values finite', () => {
    const style = cropViewStyle({ x: NaN, y: Infinity, width: NaN, height: -1 }, NaN, 0)
    expect(JSON.stringify(style)).not.toMatch(/NaN|Infinity/)
  })

  it('assembles complete audio chunks in order and preserves the webm envelope', async () => {
    const audio = recordedAudio([new Blob(['header']), new Blob(), new Blob(['frames']), new Blob(['tail'])])
    expect(audio.type).toBe('audio/webm')
    expect(audio.size).toBe(16)
    expect(await readBlob(audio)).toBe('headerframestail')
    expect(recordedAudio([]).size).toBe(0)
  })
})

describe('microphone mode lifecycle without a capture service', () => {
  it('owns listeners independently and cleans the listening state on disposal', () => {
    const session = new MicCaptureSession()
    const states: string[] = []
    const submissions: string[] = []
    const unsubscribe = session.subscribe(() => states.push(JSON.stringify(session.getSnapshot())))
    session.callbacks = { handsFree: {
      confirmationPhrases: phrases.confirmation, exitPhrases: phrases.exit,
      onSubmit: text => submissions.push(text),
      enabled: true, muted: true,
    } }
    session.activate()
    expect(session.getSnapshot()).toEqual({ state: 'idle', listening: true })
    session.drain()
    expect(session.getSnapshot().listening).toBe(true)
    session.dispose()
    expect(session.getSnapshot()).toEqual({ state: 'idle', listening: false })
    expect(states).toHaveLength(2)
    unsubscribe()
    session.activate()
    expect(states).toHaveLength(2)
    session.dispose()
    expect(submissions).toEqual([])
    expect(HANDS_FREE_SEGMENT_MS).toBe(4000)
  })

  it('does not start recording without the host transcription capability', () => {
    function UnavailableRecorder() {
      const mic = useMicRecorder()
      return <button onClick={mic.toggle}>{mic.state}:{String(mic.listening)}</button>
    }
    const view = render(<StrictMode><UnavailableRecorder /></StrictMode>)
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByRole('button')).toHaveTextContent('idle:false')
    view.unmount()
  })
})

describe('spoken trigger boundaries', () => {
  it('matches configurable contractions and long phrases as whole tokens', () => {
    expect(isConfirmation("don't wait", ["don't wait"], 1)).toBe(true)
    expect(isConfirmation('please do not wait for another confirmation before sending', ['do not wait for another confirmation before sending'])).toBe(true)
    expect(isConfirmation('dont wait', ["don't wait"])).toBe(false)
  })

  it('keeps words after a tail-window trigger in the submitted instruction', () => {
    expect(accumulateTranscript('draft', 'send it please', phrases)).toEqual({ action: 'submit', buffer: 'draft send it please' })
  })

  it('retains punctuation outside the recognized suffix separators', () => {
    expect(stripTrailingPhrase('draft — send it!', phrases.confirmation)).toBe('draft —')
    expect(stripTrailingPhrase('draft send it…', phrases.confirmation)).toBe('draft send it…')
    expect(stripTrailingPhrase('draft: SEND, IT!?', phrases.confirmation)).toBe('draft')
  })

  it('uses configured phrase priority when several triggers occur in the tail', () => {
    expect(stripTrailingPhrase('send it then go ahead', phrases.confirmation)).toBe('send it then go ahead')
    expect(stripTrailingPhrase('send it then go ahead', [...phrases.confirmation].reverse())).toBe('send it then')
  })
})

describe('send action precedence across every input combination', () => {
  const keys: Array<keyof SendButtonInputs> = ['processing', 'streaming', 'canSend', 'canQueue', 'justSent']
  const expected = [
    'send-disabled', 'processing', 'stop', 'processing', 'send', 'processing', 'stop', 'processing',
    'send-disabled', 'processing', 'stop', 'processing', 'send', 'processing', 'steer', 'processing',
    'sent', 'processing', 'stop', 'processing', 'sent', 'processing', 'stop', 'processing',
    'sent', 'processing', 'stop', 'processing', 'sent', 'processing', 'steer', 'processing',
  ]
  it.each(expected.map((kind, mask) => [mask, kind] as const))('combination %s resolves to %s', (mask, kind) => {
    const input = Object.fromEntries(keys.map((key, index) => [key, !!(mask & (1 << index))])) as unknown as SendButtonInputs
    const actual = resolveSendButton(input)
    expect(actual).toBe(kind)
    expect(sendButtonIsActive(actual)).toBe(['send', 'stop', 'steer'].includes(kind))
  })
})
