import { LoadError } from '../../shared/ui/ListScaffold'
import { Sparkles, RefreshCw, Lightbulb, Brain } from 'lucide-react'
import { Surface } from '../../shared/ui/Surface'
import { useQuery } from '../../shared/data/data'
import { api, type LearningSummary, type LearningSummaryGroup } from '../../shared/data/api'
import { fvs } from '../../shared/theme/fontWeight'

const sections = [
  { key: 'new_skills', label: 'new', icon: Sparkles },
  { key: 'refined_skills', label: 'refined', icon: RefreshCw },
  { key: 'pending_proposals', label: 'pending', icon: Lightbulb },
  { key: 'facts', label: 'facts', icon: Brain },
] as const
function SummaryRow({ icon, label, group }: { icon: React.ReactNode; label: string; group: LearningSummaryGroup }) {
  const extra = Math.max(0, group.count - group.names.length)
  const sample = group.names.join(', ')
  return <div className="flex items-start gap-s text-[0.8125rem] leading-snug" title={group.names.join('\n')}>
    <span className="mt-px shrink-0 opacity-70">{icon}</span><span className="shrink-0 text-on-surface-var" style={fvs(600)}>{group.count} {label}</span>
    {sample && <span className="min-w-0 flex-1 truncate text-on-surface-low">{sample}{extra > 0 ? ` +${extra} more` : ''}</span>}
  </div>
}
export function LearningSummaryBlock() {
  const { data, error: loadErr, refresh } = useQuery<LearningSummary | null>('learning:summary', () => api.learningSummary())
  if (loadErr) return <LoadError what="learning summary" error={loadErr} onRetry={refresh} />
  if (!data || data.total <= 0) return null
  const visible = sections.filter(section => data[section.key].count > 0)
  return <Surface tone="low" radius="lg" className="mb-l border border-outline-variant/25 px-m py-m">
    <section role="region" aria-label={`Learned in the last ${data.window_days} days`} className="grid gap-s">
      <h2 className="text-[0.75rem] uppercase tracking-wide text-on-surface-low/80" style={fvs(600)}>Learned in the last {data.window_days} days</h2>
      {visible.map(({ key, label, icon: Icon }) => <SummaryRow key={key} icon={<Icon size={13} />} label={label} group={data[key]} />)}
    </section>
  </Surface>
}
