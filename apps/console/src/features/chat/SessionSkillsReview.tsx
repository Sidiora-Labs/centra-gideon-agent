import { LoadError } from '../../shared/ui/ListScaffold'
import { useEffect, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { GraduationCap, Check, X, Pencil } from 'lucide-react'
import { api, type EphemeralDraft } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { Modal } from '../../shared/ui/Modal'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function SessionSkillsReview({ sessionKey, agent, refreshKey }: {
  sessionKey: string
  agent?: string
  refreshKey: number
}) {
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [drafts, setDrafts] = useState<EphemeralDraft[]>([])
  const [open, setOpen] = useState(false)

  const load = () => {
    if (!sessionKey) return
    setLoadErr(null)
    api.ephemeralSkills(sessionKey).then(setDrafts).catch(setLoadErr)
  }
  useEffect(load, [sessionKey, refreshKey])

  if (loadErr) return <LoadError what="session skills" error={loadErr} onRetry={load} />
  if (drafts.length === 0) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-type="caption"
        className="inline-flex items-center gap-1.5 rounded-pill bg-surface-container px-3 h-7 text-on-surface-low hover:text-on-surface transition-colors"
        title="Review skills taught this session"
      >
        <GraduationCap size={14} style={{ color: 'var(--color-primary)' }} />
        {drafts.length} session skill{drafts.length === 1 ? '' : 's'} to review
      </button>
      {open && (
        <SessionSkillsModal
          sessionKey={sessionKey}
          agent={agent}
          drafts={drafts}
          onClose={() => setOpen(false)}
          onChanged={load}
        />
      )}
    </>
  )
}

function SessionSkillsModal({ sessionKey, agent, drafts, onClose, onChanged }: {
  sessionKey: string
  agent?: string
  drafts: EphemeralDraft[]
  onClose: () => void
  onChanged: () => void
}) {
  return (
    <Modal
      title="Skills taught this session"
      icon={<GraduationCap size={18} style={{ color: 'var(--color-primary)' }} />}
      onClose={onClose}
    >
      <p data-type="body-s" className="mb-4 text-on-surface-low">
        Keep any of these for later? Save to just this agent, to all agents, or forget it.
      </p>
      <div className="flex flex-col gap-3">
        {drafts.map((d) => (
          <DraftCard key={d.slug} sessionKey={sessionKey} agent={agent} draft={d} onChanged={onChanged} />
        ))}
      </div>
      <div className="mt-4 flex justify-end">
        <Button variant="ghost" size="sm" onClick={onClose}>Done</Button>
      </div>
    </Modal>
  )
}

function DraftCard({ sessionKey, agent, draft, onChanged }: {
  sessionKey: string
  agent?: string
  draft: EphemeralDraft
  onChanged: () => void
}) {
  const [title, setTitle] = useState(draft.title)
  const [body, setBody] = useState(draft.body)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState('')
  const [done, setDone] = useState('')

  const promote = async (scope: 'agent' | 'global') => {
    setBusy(scope)
    try {
      const r = await api.promoteEphemeralSkill(sessionKey, {
        slug: draft.slug, scope, agent: scope === 'agent' ? agent : undefined,
        title: title !== draft.title ? title : undefined,
        body: body !== draft.body ? body : undefined,
      })
      setDone(scope === 'agent' ? `Saved to ${agent || 'this agent'}` : 'Saved for all agents')
      void r; onChanged()
    } catch (e) {
      setDone(e instanceof Error ? e.message : 'Failed')
    }
    setBusy('')
  }
  const forget = async () => {
    setBusy('forget')
    try { await api.discardEphemeralSkill(sessionKey, draft.slug); setDone('Forgotten'); onChanged() }
    catch { setDone('Failed') }
    setBusy('')
  }

  if (done) {
    return (
      <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-low flex items-center gap-2">
        <Check size={14} className="text-ok" /> {title} — {done}
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-container p-3">
      {editing ? (
        <>
          <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Skill title"
            data-type="body-s" className="mb-2 h-8 w-full rounded-md bg-surface-high px-2.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} aria-label="Skill body"
            data-type="body-s" className="mb-2 w-full rounded-md bg-surface-high px-2.5 py-1.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        </>
      ) : (
        <div className="mb-2">
          <div className="flex items-center gap-1.5">
            <span data-type="title-m" className="text-on-surface" style={fvs(500)}>{title}</span>
            <button type="button" onClick={() => setEditing(true)} className="text-on-surface-low hover:text-on-surface" title="Edit"><Pencil size={12} /></button>
          </div>
          <p data-type="caption" className="mt-0.5 line-clamp-3 whitespace-pre-wrap text-on-surface-low">{body}</p>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => promote('agent')} loading={busy === 'agent'} disabled={!!busy}>This agent
        </Button>
        <Button variant="secondary" size="sm" onClick={() => promote('global')} loading={busy === 'global'} disabled={!!busy}>All agents
        </Button>
        <Button variant="ghost" size="sm" onClick={forget} disabled={!!busy} disabledReason={BUSY_REASON}>
          <X size={13} /> Forget
        </Button>
      </div>
    </div>
  )
}
