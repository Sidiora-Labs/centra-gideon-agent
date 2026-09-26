import { useMemo, useState } from 'react'
import { withWeight } from '../../shared/theme/fontWeight'
import { Check, Ban, Pencil, ShieldCheck, ShieldAlert, AlertTriangle } from 'lucide-react'
import { ApprovalPrompt } from '../../shared/ui/ApprovalPrompt'
import { RungChip } from '../../shared/ui/RungChip'
import { Segmented } from '../../shared/ui/Segmented'
import { providerRungIndex, useAutonomyLadder } from '../../shared/data/rungs'
import { approvalOutcome } from './approvalOutcome'
import { deriveBlastRadius, establishedFacets } from './approvalMeta'
import type { ApprovalSegment } from './chatTypes'

const RISK_META = {
  safe: { label: 'Safe', icon: ShieldCheck, color: 'var(--color-ok)' },
  caution: { label: 'Caution', icon: AlertTriangle, color: 'var(--color-warn)' },
  destructive: { label: 'Destructive', icon: ShieldAlert, color: 'var(--color-danger)' },
} as const

function RiskChip({ risk }: { risk: NonNullable<ApprovalSegment['risk']> }) {
  const m = RISK_META[risk]
  if (!m) return null
  const Icon = m.icon
  return (
    <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-1.5 h-[18px] shrink-0"
      title={`Risk: ${m.label}`}
      style={withWeight({ background: `color-mix(in srgb, ${m.color} 16%, transparent)`, color: m.color }, 600)}>
      <Icon size={11} aria-hidden /> {m.label}
    </span>
  )
}

function BlastRadiusChips({ tool, risk }: { tool: string; risk?: ApprovalSegment['risk'] }) {
  const facets = establishedFacets(deriveBlastRadius({ tool, risk: risk ?? undefined }))
  if (facets.length === 0) return null
  return (
    <ul aria-label="What this can touch, as far as we can establish"
      className="mt-1.5 flex list-none flex-wrap items-center gap-1 p-0">
      {facets.map((f) => (
        <li key={f.key} title={f.detail}
          data-type="caption" className="inline-flex items-center rounded-pill bg-surface-high px-1.5 h-[18px] text-on-surface-var">
          {f.label}
        </li>
      ))}
    </ul>
  )
}

type Action = 'approved' | 'rejected' | 'revised' | 'trust' | 'trust_agent'

const REMEMBER_SCOPES = [
  {
    key: 'once',
    label: 'Just this once',
    action: 'approved' as const,
    promise: 'Nothing is remembered. The next tool call asks again.',
  },
  {
    key: 'chat',
    label: 'This chat',
    action: 'trust' as const,
    promise: 'Every tool in this chat runs without asking, until you change it back.',
  },
  {
    key: 'agent',
    label: 'This agent',
    action: 'trust_agent' as const,
    promise: 'Saved on this agent: every tool runs without asking, in this chat and future ones.',
  },
] as const

type RememberScope = (typeof REMEMBER_SCOPES)[number]['key']

export function ApprovalCard({ seg, onAct }: { seg: ApprovalSegment; onAct: (id: string, action: Action, revision?: string) => void }) {
  const [scope, setScope] = useState<RememberScope>('once')
  const [revision, setRevision] = useState('')
  const { ladder } = useAutonomyLadder()
  const rungType = useMemo(() => providerRungIndex(ladder).get(seg.tool), [ladder, seg.tool])
  if (seg.resolved) {
    const { label, icon: Icon, tone } = approvalOutcome(seg.resolved)
    return (
      <div data-type="caption" className="my-1 flex items-center gap-1.5" style={{ color: tone }}>
        <Icon size={13} aria-hidden />
        <span>{seg.tool} — {label}</span>
      </div>
    )
  }
  const scopes = seg.toolKind && ['prompt', 'write', 'record'].includes(seg.toolKind)
    ? REMEMBER_SCOPES
    : REMEMBER_SCOPES.slice(0, 2)
  const chosen = scopes.find((s) => s.key === scope) ?? scopes[0]
  return (
    <ApprovalPrompt
      tool={seg.tool}
      args={seg.input}
      purpose={seg.purpose}
      badge={
        rungType || seg.risk ? (
          <span className="inline-flex items-center gap-1.5">
            {rungType && <RungChip type={rungType} ladder={ladder} />}
            {seg.risk && <RiskChip risk={seg.risk} />}
          </span>
        ) : undefined
      }
      meta={<BlastRadiusChips tool={seg.tool} risk={seg.risk} />}
      scope={
        <div className="mt-2 flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span data-type="caption" className="text-on-surface-low">Remember this choice</span>
            <Segmented size="sm" ariaLabel="Remember this choice"
              options={scopes.map((s) => ({ key: s.key, label: s.label, title: s.promise }))}
              value={scope} onChange={(k) => setScope(k as RememberScope)} />
          </div>
          {
}
          <p aria-live="polite" data-type="caption" className="text-on-surface-low">{chosen.promise}</p>
          {seg.canRevise && <label className="mt-2 flex flex-col gap-1 text-on-surface-var" data-type="caption">
            Request a change before this action runs
            <textarea aria-label="How should this action change?" value={revision} maxLength={4000}
              onChange={event => setRevision(event.target.value)} rows={2}
              className="w-full resize-y rounded-md border border-outline bg-surface px-2 py-1 text-on-surface"
              placeholder="Describe what to change" />
          </label>}
        </div>
      }
      choices={[
        {
          key: 'allow', icon: Check, label: 'Allow',
          name: `Allow ${seg.tool} — ${chosen.label.toLowerCase()}: ${chosen.promise}`,
          onClick: () => onAct(seg.id, chosen.action),
        },
        {
          key: 'rejected', icon: Ban, label: 'Deny', tone: 'danger',
          name: `Deny ${seg.tool} — nothing is remembered`,
          onClick: () => onAct(seg.id, 'rejected'),
        },
        ...(seg.canRevise ? [{
          key: 'revised', icon: Pencil, label: 'Request change',
          name: `Request a revised ${seg.tool} action without running this one`,
          busy: !revision.trim(),
          onClick: () => onAct(seg.id, 'revised', revision.trim()),
        }] : []),
      ]}
    />
  )
}

// Exported for the test that enumerates the scope vocabulary as a CLOSED set: every option
export { REMEMBER_SCOPES, type RememberScope }
