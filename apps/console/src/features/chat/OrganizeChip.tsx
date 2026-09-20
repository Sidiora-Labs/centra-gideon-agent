import { useEffect, useState } from 'react'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { motion } from 'framer-motion'
import { FolderTree, X } from 'lucide-react'
import { spring } from '../../shared/theme/motion'
import { fvs } from '../../shared/theme/fontWeight'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { api, type OrganizeProposal } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'

export function OrganizeChip({ sessionKey, refreshKey, onApplied }: {
  sessionKey: string
  refreshKey?: number
  onApplied?: (applied: { folder_id: string; tags: string[] }) => void
}) {
  const [proposal, setProposal] = useState<OrganizeProposal | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!sessionKey) { setProposal(null); return }
    let live = true
    api.organizeSuggestion(sessionKey)
      .then((r) => { if (live) setProposal(r?.proposal ?? null) })
      .catch(() => { if (live) setProposal(null) })
    return () => { live = false }
  }, [sessionKey, refreshKey])

  if (!proposal) return null

  const accept = async () => {
    setBusy(true)
    try {
      const r = await api.organizeAccept(sessionKey, proposal)
      notify(proposal.folder_name ? `Filed in ${proposal.folder_name}` : 'Chat tagged', 'success')
      onApplied?.({ folder_id: r?.folder_id ?? '', tags: r?.tags ?? [] })
      setProposal(null)
    } catch (e) {
      notify(`Couldn't organize: ${String((e as Error)?.message || e)}`, 'error')
      setBusy(false)
    }
  }

  const decline = () => {
    void reportingWrite('decline that suggestion', () => api.organizeDecline(sessionKey, proposal))
    setProposal(null)
  }

  const parts: string[] = []
  if (proposal.folder_name) parts.push(proposal.folder_name)
  if (proposal.tags.length) parts.push(proposal.tags.join(', '))

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
      transition={spring.spatialFast}
      data-type="label-s"
      className="inline-flex items-center gap-2 rounded-pill border border-outline-variant/50 bg-surface-container pl-3 pr-1.5 h-8"
      role="status">
      <FolderTree size={14} style={{ color: 'var(--color-primary)' }} className="shrink-0" />
      <span className="text-on-surface-var">
        Organize this chat under{' '}
        <span className="text-on-surface" style={fvs(600)}>{parts.join(' · ')}</span>
        {proposal.reason ? ` — ${proposal.reason}?` : '?'}
      </span>
      <Button variant="secondary" size="xs" onClick={accept} loading={busy} className="h-6 px-m">File it</Button>
      <IconButton icon={X} label="No thanks (won't suggest this again)" onClick={decline} size={24} iconSize={13} />
    </motion.div>
  )
}
