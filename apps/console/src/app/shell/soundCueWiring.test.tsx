
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, act } from '@testing-library/react'
import { renderHook } from '@testing-library/react'

import { Toaster } from '../../shared/ui/Toaster'
import { useApprovalToasts } from './useApprovalToasts'
import type { WsMessage } from '../../shared/data/useChatSocket'

const playCue = vi.fn()
vi.mock('../../shared/theme/soundCues', () => ({
  playCue: (name: string) => playCue(name),
  armCueAudio: () => {},
  soundCuesEnabled: () => false,
  setSoundCuesEnabled: () => {},
}))

let onMessage: ((m: WsMessage) => void) | null = null
vi.mock('../../shared/data/useChatSocket', () => ({
  useChatSocket: (cb: (m: WsMessage) => void) => {
    onMessage = cb
  },
}))

beforeEach(() => {
  playCue.mockClear()
  onMessage = null
})

const SRC = join(process.cwd(), "src")

function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

function blockAt(src: string, from: number): string {
  const open = src.indexOf('{', from)
  let depth = 0
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++
    else if (src[i] === '}') {
      depth--
      if (depth === 0) return src.slice(open + 1, i)
    }
  }
  throw new Error('unbalanced block')
}

describe('turn settled → ChatPage', () => {
  const code = stripComments(readFileSync(join(SRC, 'features/ChatPage.tsx'), 'utf8'))

  it('the comment stripper actually strips (or every assertion below is vacuous)', () => {
    expect(stripComments('a /* playCue("x") */ b')).not.toMatch(/playCue/)
    expect(stripComments('a // playCue("x")\nb')).not.toMatch(/playCue/)
    expect(stripComments("playCue('turn_complete')")).toMatch(/playCue\('turn_complete'\)/)
  })

  it('imports the cue from the design module, not a local re-implementation', () => {
    expect(code).toMatch(/import \{ playCue \} from '\.\.\/shared\/theme\/soundCues'/)
  })

  it('fires inside markStreaming’s streaming→settled branch, beside the skills epoch', () => {
    const at = code.indexOf('const markStreaming = ')
    expect(at, 'markStreaming must still exist — the settle point is the whole cue site').toBeGreaterThan(0)
    const body = blockAt(code, at)
    expect(body, 'the extracted body must be real code').toMatch(/setStreaming\(v\)/)

    const branch = blockAt(body, body.indexOf('if (streamingRef.current && !v)'))
    expect(branch).toMatch(/setSessionSkillsEpoch/)
    expect(branch, 'the cue belongs in the settle branch, not on every render').toMatch(
      /playCue\('turn_complete'\)/,
    )
    expect(code.match(/playCue\(/g)?.length).toBe(1)
  })
})

describe('approval requested → useApprovalToasts', () => {
  const frame = (over: Record<string, unknown> = {}): WsMessage => ({
    type: 'approval',
    data: { session: 'other-session', id: 'ap-1', tool: 'Bash', ...over },
  })

  it('cues when an approval lands for a session the user is not looking at', () => {
    renderHook(() => useApprovalToasts(''))
    expect(onMessage, 'the socket handler must be captured or nothing is driven').not.toBeNull()
    act(() => onMessage!(frame()))
    expect(playCue).toHaveBeenCalledWith('approval_needed')
  })

  it('stays silent for the session already on screen — the card is right there', () => {
    renderHook(() => useApprovalToasts('other-session'))
    act(() => onMessage!(frame()))
    expect(playCue).not.toHaveBeenCalled()
  })

  it('cues ONCE per approval, not again on a reconnect re-broadcast', () => {
    renderHook(() => useApprovalToasts(''))
    act(() => onMessage!(frame()))
    act(() => onMessage!(frame()))
    expect(playCue).toHaveBeenCalledTimes(1)
  })

  it('stays silent for a non-approval frame', () => {
    renderHook(() => useApprovalToasts(''))
    act(() => onMessage!({ type: 'token', data: { session: 's', text: 'hi' } }))
    expect(playCue).not.toHaveBeenCalled()
  })
})

describe('error toast → Toaster', () => {
  const toast = (level: string, message = 'something failed') => {
    act(() => {
      window.dispatchEvent(new CustomEvent('ne:toast', { detail: { level, message } }))
    })
  }

  it('cues on an error toast', () => {
    render(<Toaster />)
    toast('error')
    expect(playCue).toHaveBeenCalledWith('error')
  })

  it('does NOT cue on info or success — a chime on every "Saved" is unusable', () => {
    render(<Toaster />)
    toast('info')
    toast('success')
    toast('banana')
    expect(playCue).not.toHaveBeenCalled()
  })

  it('cues per error, matching the toast the user sees', () => {
    render(<Toaster />)
    toast('error', 'first')
    toast('error', 'second')
    expect(playCue).toHaveBeenCalledTimes(2)
  })

  it('stays silent for an empty message the host drops anyway', () => {
    render(<Toaster />)
    toast('error', '')
    expect(playCue).not.toHaveBeenCalled()
  })
})

describe('the shell arms the gesture primer', () => {
  it('App calls armCueAudio in an effect — cues need a gesture the cue points are not', () => {
    const code = stripComments(readFileSync(join(SRC, 'app/shell/App.tsx'), 'utf8'))
    expect(code).toMatch(/import \{ armCueAudio \} from '\.\.\/\.\.\/shared\/theme\/soundCues'/)
    expect(code).toMatch(/useEffect\(\(\) => \{ armCueAudio\(\) \}, \[\]\)/)
  })
})
