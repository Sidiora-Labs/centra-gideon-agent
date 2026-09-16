import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { desktopBridge } from './desktopBridge'

export interface PushToTalkPush {
  action: 'toggle' | 'stop'
  reason?: string
}

export const DEFAULT_PUSH_TO_TALK_CHORD = 'CommandOrControl+Shift+Space'

const MOD_SYMBOL: Record<string, string> = {
  CommandOrControl: '⌘',
  CmdOrCtrl: '⌘',
  Command: '⌘',
  Cmd: '⌘',
  Control: '⌃',
  Ctrl: '⌃',
  Alt: '⌥',
  Option: '⌥',
  Shift: '⇧',
  Super: '⌘',
  Meta: '⌘',
}

export function formatChord(chord: string): string {
  const parts = chord.split('+').map((p) => p.trim()).filter(Boolean)
  if (!parts.length) return ''
  const key = parts[parts.length - 1]
  const mods = parts.slice(0, -1).map((m) => MOD_SYMBOL[m] ?? m)
  return `${mods.join('')}${key}`
}

export function chordFromEvent(e: {
  key: string
  code: string
  metaKey: boolean
  ctrlKey: boolean
  altKey: boolean
  shiftKey: boolean
}): string {
  const mods: string[] = []
  if (e.metaKey) mods.push('Command')
  if (e.ctrlKey) mods.push('Control')
  if (e.altKey) mods.push('Alt')
  if (e.shiftKey) mods.push('Shift')

  let key = ''
  if (e.code.startsWith('Key')) key = e.code.slice(3)
  else if (e.code.startsWith('Digit')) key = e.code.slice(5)
  else if (/^F\d{1,2}$/.test(e.code)) key = e.code
  else if (e.code === 'Space') key = 'Space'
  else if (e.key.length === 1) key = e.key.toUpperCase()
  else if (['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'Home', 'End'].includes(e.key)) key = e.key
  else if (e.key.startsWith('Arrow')) key = e.key.slice(5)

  if (!key || ['Meta', 'Control', 'Alt', 'Shift'].includes(e.key)) return ''
  if (!mods.length) return ''
  return [...mods, key].join('+')
}

export async function ensureMicGrant(): Promise<string | null> {
  const bridge = desktopBridge()
  if (!bridge) return null
  try {
    const state = await bridge.capabilities.probe('audio_capture')
    if (state.granted === 'granted') return null
    if (state.granted === 'denied' || state.granted === 'restricted') {
      return 'Microphone access is turned off for Gideon. Turn it on in System Settings › Privacy & Security › Microphone, then try again.'
    }
    if (!state.requestable) {
      return state.reason || 'The microphone is not available on this machine.'
    }
    const grant = await bridge.capabilities.request('audio_capture')
    if (grant.granted) return null
    return grant.reason || 'Microphone access was declined, so there is nothing to record.'
  } catch {
    return null
  }
}

export function usePushToTalk({
  capturing,
  onStart,
  onStop,
  enabled = true,
}: {
  capturing: boolean
  onStart: () => void
  onStop: () => void
  enabled?: boolean
}) {
  const bridge = desktopBridge()
  const available = !!bridge && enabled
  const [chord, setChord] = useState('')
  const [bindError, setBindError] = useState('')

  const stateRef = useRef({ capturing, onStart, onStop })
  stateRef.current = { capturing, onStart, onStop }

  useEffect(() => {
    if (!available || !bridge) return
    let live = true
    const off = bridge.pushToTalk?.on?.((push) => {
      const s = stateRef.current
      if (!push || typeof push !== 'object') return
      if (push.action === 'stop') {
        if (s.capturing) s.onStop()
        return
      }
      if (push.action !== 'toggle') return
      if (s.capturing) s.onStop()
      else s.onStart()
    })

    void (async () => {
      let want = DEFAULT_PUSH_TO_TALK_CHORD
      try {
        const cfg = await api.gideonConfig()
        const v = (cfg?.voice as Record<string, unknown> | undefined)?.push_to_talk_chord
        if (typeof v === 'string' && v.trim()) want = v.trim()
      } catch {
      }
      if (!live) return
      try {
        const r = await bridge.pushToTalk?.bind?.(want)
        if (!live) return
        if (r?.ok) { setChord(r.chord); setBindError('') }
        else { setChord(''); setBindError(r?.reason || 'The push-to-talk shortcut could not be set.') }
      } catch {
        if (live) setBindError('The push-to-talk shortcut could not be set.')
      }
    })()

    return () => { live = false; off?.() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available])

  useEffect(() => {
    if (!available || !bridge) return
    void bridge.pushToTalk?.setCapturing?.(capturing)
  }, [available, bridge, capturing])

  useEffect(() => {
    if (!available || !bridge) return
    return () => { void bridge.pushToTalk?.setCapturing?.(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available])

  return { available, chord, bindError }
}

export async function bindChord(chord: string): Promise<{ ok: boolean; conflict: boolean; reason: string }> {
  const bridge = desktopBridge()
  if (!bridge?.pushToTalk?.bind) {
    return { ok: false, conflict: false, reason: 'The desktop app is not connected, so there is no global shortcut to bind.' }
  }
  try {
    const r = await bridge.pushToTalk.bind(chord)
    return { ok: !!r?.ok, conflict: !!r?.conflict, reason: r?.reason ?? '' }
  } catch (e) {
    return { ok: false, conflict: false, reason: e instanceof Error ? e.message : 'The desktop app did not answer.' }
  }
}

export type PushToTalk = ReturnType<typeof usePushToTalk>
