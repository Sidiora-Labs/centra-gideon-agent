import { useState } from 'react'
import { MessageCircleQuestion } from 'lucide-react'
import { IconButton } from './IconButton'
import { investigate } from '../data/investigate'

export function InvestigateButton({ kind, id, backLink, size = 24, label }: {
  kind: string
  id: string
  label?: string
  backLink?: string
  size?: number
}) {
  const [busy, setBusy] = useState(false)
  return (
    <IconButton icon={MessageCircleQuestion} label={label ?? 'Investigate in chat'} title="Investigate in chat"
      size={size} iconSize={Math.max(12, Math.round(size * 0.55))} loading={busy}
      onClick={(e) => {
        e.stopPropagation()
        setBusy(true)
        investigate(kind, id, backLink ? { backLink } : undefined)
          .catch(() => {   })
          .finally(() => setBusy(false))
      }} />
  )
}
