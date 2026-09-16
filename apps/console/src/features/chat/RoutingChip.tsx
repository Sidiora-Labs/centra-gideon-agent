import { useState } from 'react'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { motion } from 'framer-motion'
import { Compass, X } from 'lucide-react'
import { spring } from '../../shared/theme/motion'
import { fvs } from '../../shared/theme/fontWeight'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { api } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'

export interface RoutingSuggestion {
  session: string
  agent: string
  specialty: string
  score: number
  method: string
}

export function RoutingChip({ suggestion, defaultAgent, onRoute, onDismiss }: {
  suggestion: RoutingSuggestion
  defaultAgent: string
  onRoute: () => void
  onDismiss: () => void
}) {
  const [busy, setBusy] = useState(false)
  const producerId = `${defaultAgent || 'default'}->${suggestion.agent}`
  const targetId = `${suggestion.session}:${suggestion.agent}`

  const route = async () => {
    setBusy(true)
    try {
      await api.setSessionAgent(suggestion.session, suggestion.agent)
      api.recordFeedback({
        target_kind: 'routing_suggestion', target_id: targetId, verdict: 'up',
        producer_kind: 'routing_pair', producer_id: producerId,
        snapshot: { agent: suggestion.agent, method: suggestion.method, score: suggestion.score },
      }).catch(() => {})
      notify(`Routed to ${suggestion.agent}`, 'success')
      onRoute()
    } catch (e) {
      notify(`Couldn't route: ${String((e as Error)?.message || e)}`, 'error')
      setBusy(false)
    }
  }

  const dismiss = () => {
    void reportingWrite(`dismiss the ${suggestion.agent} suggestion`,
      () => api.routingDismiss(suggestion.agent))
    api.recordFeedback({
      target_kind: 'routing_suggestion', target_id: targetId, verdict: 'down',
      producer_kind: 'routing_pair', producer_id: producerId,
      snapshot: { agent: suggestion.agent, method: suggestion.method, score: suggestion.score },
    }).catch(() => {})
    onDismiss()
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
      transition={spring.spatialFast}
      data-type="label-s"
      className="inline-flex items-center gap-2 rounded-pill border border-outline-variant/50 bg-surface-container pl-3 pr-1.5 h-8"
      role="status">
      <Compass size={14} style={{ color: 'var(--color-primary)' }} className="shrink-0" />
      <span className="text-on-surface-var">
        <span className="text-on-surface" style={fvs(600)}>{suggestion.agent}</span>
        {suggestion.specialty ? ` handles this` : ' may fit better'} — route this chat to it?
      </span>
      <Button variant="secondary" size="xs" onClick={route} loading={busy} className="h-6 px-3">Route</Button>
      <IconButton icon={X} label="Not now (won't ask again for a while)" onClick={dismiss} size={24} iconSize={13} />
    </motion.div>
  )
}
