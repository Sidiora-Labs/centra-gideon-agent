import { useEffect, useRef } from 'react'

type DetachableDiffEditor = { setModel: (model: null) => void }

export function useDiffTeardown(): (editor: DetachableDiffEditor) => void {
  const ref = useRef<DetachableDiffEditor | null>(null)
  useEffect(
    () => () => {
      try {
        ref.current?.setModel(null)
      } catch {
      }
      ref.current = null
    },
    [],
  )
  return (editor: DetachableDiffEditor) => {
    ref.current = editor
  }
}
