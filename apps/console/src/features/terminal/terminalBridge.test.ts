import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  registerTerminal,
  unregisterTerminal,
  runInTerminal,
  runInTerminalWhenReady,
} from './terminalBridge'

afterEach(() => {
  for (const id of ['t1', 't2']) unregisterTerminal(id)
  vi.useRealTimers()
})

describe('terminal registration lifecycle', () => {
  it('removes an exited pane and routes future commands to the remaining active pane', () => {
    const delivered: string[] = []
    registerTerminal('t1', (text) => { delivered.push(`t1:${text}`); return true })
    const unregisterExited = registerTerminal('t2', (text) => { delivered.push(`t2:${text}`); return true })

    unregisterExited()

    expect(runInTerminal('pwd', 't2')).toBe(false)
    expect(runInTerminal('pwd')).toBe(true)
    expect(delivered).toEqual(['t1:pwd\n'])
  })

  it('does not let an old pane unregister a newer registration with the same session id', () => {
    const delivered: string[] = []
    const unregisterOld = registerTerminal('t1', () => false)
    registerTerminal('t1', (text) => { delivered.push(text); return true })

    unregisterOld()

    expect(runInTerminal('echo ready', 't1')).toBe(true)
    expect(delivered).toEqual(['echo ready\n'])
  })
})

describe('a dropped command says so on screen', () => {
  it('raises a toast when the terminal never becomes ready, not just a console warning', () => {
    vi.useFakeTimers()
    const toasts: { message: string; level: string }[] = []
    const onToast = (e: Event) => toasts.push((e as CustomEvent).detail)
    window.addEventListener('ne:toast', onToast)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      runInTerminalWhenReady('pytest -x', () => 'never-live')
      expect(toasts, 'silent while it is still retrying').toHaveLength(0)
      vi.advanceTimersByTime(15_100)
      expect(toasts, 'exactly one toast, once').toHaveLength(1)
      expect(toasts[0].level, 'a dropped user action is an error, not info').toBe('error')
      expect(toasts[0].message).toMatch(/never became ready/)
      expect(toasts[0].message, 'and it says what to do next').toMatch(/Open a terminal and try again/)
      expect(warn).toHaveBeenCalledTimes(1)
      expect(String(warn.mock.calls[0][1])).toBe('pytest -x')
    } finally {
      window.removeEventListener('ne:toast', onToast)
      warn.mockRestore()
    }
  })

  it('says nothing when the command DOES land — no toast on the happy path', () => {
    vi.useFakeTimers()
    const toasts: unknown[] = []
    const onToast = (e: Event) => toasts.push((e as CustomEvent).detail)
    window.addEventListener('ne:toast', onToast)
    try {
      let open = false
      registerTerminal('t1', (t) => (open ? (delivered.push(t), true) : false))
      const delivered: string[] = []
      runInTerminalWhenReady('ls', () => 't1')
      open = true
      vi.advanceTimersByTime(300)
      expect(toasts, 'a successful run must stay quiet').toHaveLength(0)
      vi.advanceTimersByTime(20_000)
      expect(toasts, 'and must not toast later either').toHaveLength(0)
    } finally {
      window.removeEventListener('ne:toast', onToast)
    }
  })
})

describe('runInTerminalWhenReady', () => {
  it('retries a sender that is registered but not yet OPEN, then delivers exactly once', () => {
    vi.useFakeTimers()
    const delivered: string[] = []
    let open = false
    registerTerminal('t1', (text) => {
      if (!open) return false
      delivered.push(text)
      return true
    })
    expect(runInTerminal('pytest', 't1')).toBe(false)

    const cancel = runInTerminalWhenReady('pytest', () => 't1')
    vi.advanceTimersByTime(350)
    expect(delivered).toEqual([])
    open = true
    vi.advanceTimersByTime(200)
    expect(delivered).toEqual(['pytest\n'])
    vi.advanceTimersByTime(2000)
    expect(delivered).toEqual(['pytest\n'])
    cancel()
  })

  it('re-resolves the target id each attempt (cockpit Restart mints a new session mid-wait)', () => {
    vi.useFakeTimers()
    const delivered: string[] = []
    let live = 't1'
    registerTerminal('t1', () => false)
    runInTerminalWhenReady('make test', () => live)
    vi.advanceTimersByTime(300)
    registerTerminal('t2', (text) => { delivered.push(text); return true })
    live = 't2'
    vi.advanceTimersByTime(200)
    expect(delivered).toEqual(['make test\n'])
  })

  it('gives up after the 15s cap with a console.warn instead of spinning forever', () => {
    vi.useFakeTimers()
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    registerTerminal('t1', () => false)
    runInTerminalWhenReady('npm run build', () => 't1')
    vi.advanceTimersByTime(16_000)
    expect(warn).toHaveBeenCalledWith(
      'Run command dropped — terminal never became ready:', 'npm run build')
    warn.mockRestore()
  })

  it('cancel() stops the retry loop (unmount mid-wait must not fire into a later terminal)', () => {
    vi.useFakeTimers()
    const delivered: string[] = []
    let open = false
    registerTerminal('t1', (text) => { if (!open) return false; delivered.push(text); return true })
    const cancel = runInTerminalWhenReady('ls', () => 't1')
    vi.advanceTimersByTime(200)
    cancel()
    open = true
    vi.advanceTimersByTime(2000)
    expect(delivered).toEqual([])
  })
})
