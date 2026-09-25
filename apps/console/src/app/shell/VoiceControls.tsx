import { useEffect, useRef } from 'react'
import SpokenNavigation, { type SpokenNavigationProps } from '../../features/capabilities/experience/SpokenNavigation'
import ProactiveSpeech from '../../features/capabilities/experience/ProactiveSpeech'
import { Button } from '../../shared/ui/Button'

type Props = SpokenNavigationProps & { open: boolean; onClose: () => void }
export default function VoiceControls({ open, onClose, ...navigation }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (open && !element.open) element.showModal()
    if (!open && element.open) element.close()
  }, [open])
  return <dialog ref={dialog} onCancel={onClose} onClose={onClose} aria-labelledby="voice-controls-title"
    className="rounded-xl border border-outline-variant bg-surface text-on-surface p-6 space-y-6" style={{ maxWidth: 'min(42rem, calc(100vw - 2rem))', maxHeight: 'calc(100vh - 2rem)', overflow: 'auto' }}>
    <header className="flex items-center justify-between gap-4"><h2 id="voice-controls-title">Voice controls</h2><Button onClick={onClose}>Close voice controls</Button></header>
    <SpokenNavigation {...navigation}/>
    <ProactiveSpeech/>
  </dialog>
}
