import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent, type PointerEvent } from 'react'
import type { ComposerProps } from './types'
import { COMPOSER_HEIGHT, ComposerFileDrop, ComposerResizeSession, composerAction, keyboardComposerHeight, restoredComposerHeight } from './composerSurfaceState'

type SurfaceOptions = Pick<ComposerProps, 'value' | 'minChars' | 'processing' | 'streaming' | 'canQueue' | 'onSend' | 'onAttach' | 'onFocusChange'> & { attach: boolean }

export function useComposerSurface(options: SurfaceOptions) {
  const current = useRef(options)
  current.current = options
  const [height, setHeight] = useState(() => {
    try { return restoredComposerHeight(localStorage.getItem(COMPOSER_HEIGHT.key)) }
    catch { return COMPOSER_HEIGHT.initial as number }
  })
  const [focused, setFocused] = useState(false)
  const [dropping, setDropping] = useState(false)
  const [justSent, setJustSent] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const drop = useRef(new ComposerFileDrop())
  const resize = useRef(new ComposerResizeSession())
  const detach = useRef<(() => void) | undefined>(undefined)
  const draftReady = options.value.trim().length >= (options.minChars ?? 1)
  const action = composerAction({ canSend: draftReady, processing: !!options.processing, streaming: !!options.streaming, canQueue: !!options.canQueue, justSent })

  useEffect(() => {
    try { localStorage.setItem(COMPOSER_HEIGHT.key, String(height)) } catch { /* Keep the current size when storage is unavailable. */ }
  }, [height])
  useEffect(() => () => { clearTimeout(timer.current); detach.current?.() }, [])
  useEffect(() => {
    if (!options.attach || !options.onAttach) { drop.current.reset(); setDropping(false) }
  }, [options.attach, options.onAttach])

  const submit = () => {
    if (!action.canSubmit) return
    current.current.onSend()
    if (!action.celebrate) return
    clearTimeout(timer.current)
    setJustSent(true)
    timer.current = setTimeout(() => setJustSent(false), 620)
  }

  const focus = (next: boolean) => {
    setFocused(next)
    current.current.onFocusChange?.(next)
  }
  const onPointerDown = (event: PointerEvent) => {
    if (event.button > 0) return
    event.preventDefault()
    detach.current?.()
    resize.current.begin(event.clientY, height)
    const pointer = event.pointerId
    const move = (next: globalThis.PointerEvent) => {
      if (next.pointerId !== pointer) return
      const value = resize.current.move(next.clientY)
      if (value !== undefined) setHeight(value)
    }
    const finish = () => {
      resize.current.end()
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
      window.removeEventListener('blur', finish)
      detach.current = undefined
    }
    detach.current = finish
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
    window.addEventListener('blur', finish)
  }
  const onKeyDown = (event: KeyboardEvent) => {
    const next = keyboardComposerHeight(event.key, height, event.shiftKey)
    if (next === undefined) return
    event.preventDefault()
    setHeight(next)
  }
  const acceptsFiles = () => !!current.current.attach && !!current.current.onAttach
  const onDragEnter = (event: DragEvent) => {
    if (!acceptsFiles() || !drop.current.enter(Array.from(event.dataTransfer?.types ?? []))) return
    event.preventDefault()
    setDropping(true)
  }
  const onDragOver = (event: DragEvent) => {
    if (acceptsFiles()) event.preventDefault()
  }
  const onDragLeave = (event: DragEvent) => {
    if (!acceptsFiles()) return
    event.preventDefault()
    setDropping(drop.current.leave())
  }
  const onDrop = (event: DragEvent) => {
    if (!acceptsFiles()) return
    event.preventDefault()
    const files = drop.current.receive(Array.from(event.dataTransfer?.files ?? []))
    setDropping(false)
    if (files.length) current.current.onAttach?.(files)
  }
  return {
    height, focused, dropping, draftReady, action, submit, focus,
    resizeBindings: { onPointerDown, onKeyDown },
    dropBindings: { onDragEnter, onDragOver, onDragLeave, onDrop },
  }
}
