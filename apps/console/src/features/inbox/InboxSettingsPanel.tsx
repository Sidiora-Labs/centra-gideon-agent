import { InboxConfigBoundary } from './InboxConfigBoundary'
import { useInboxSettingsState } from './inboxSettingsState'
import { Loading, LoadError } from '../../shared/ui/ListScaffold'
import { Row, Field, Toggle, SavedToast } from '../settings/settingsUI'
import { NumberField } from '../../shared/ui/forms'
import { TextLink } from '../../shared/ui/TextLink'

export function InboxSettingsPanel() {
  const { s, saved, cfgErr, cfgLoading, retryConfig, loadErr, load, engagementOn, sourcesOn, patch, setEngagement, setSources } = useInboxSettingsState()

  if (!s && loadErr) return <LoadError what="inbox settings" error={loadErr} onRetry={load} />
  if (!s) return <Loading what="inbox settings" />
  const switches = [
    { label: 'Poll message sources', hint: 'Collect messages from connected poll sources (filesystem drops; channel apps). Agents can always post here directly.', value: sourcesOn, change: setSources, aria: 'Poll message sources' },
    { label: 'Engagement ranking', hint: 'Rank the inbox by how much you engage with each channel/sender (favorites, opens, replies boost; dismisses lower) on top of recency. Off = pure newest-first.', value: engagementOn, change: setEngagement, aria: 'Engagement ranking' },
  ]
  return <div className="grid gap-l rounded-xl border border-outline/20 p-m">
    <div className="flex justify-end"><SavedToast show={saved} /></div>
    <InboxConfigBoundary cfgErr={cfgErr} loading={cfgLoading} onRetry={retryConfig}>
    {switches.map(control => <Row key={control.label} label={control.label} hint={control.hint}>
      <Toggle on={Boolean(control.value)} onChange={control.change} label={control.aria} disabled={control.value === null} />
    </Row>)}
    </InboxConfigBoundary>
    <Row label="Alerts" hint="Keyword and name-mention alerts are now per-notification-kind, so the same rules cover loops, proposals and messages alike.">
      <TextLink href="#/settings/notifications" ink="emphasis" size="sm">Open notification rules</TextLink>
    </Row>
    <Row label="Auto-cleanup" hint="Automatically prune items past the retention window.">
      <Toggle on={s.auto_cleanup_enabled} onChange={value => patch({ auto_cleanup_enabled: value })} label="Auto cleanup" />
    </Row>
    {s.auto_cleanup_enabled && <Field label="Retention (days)" hint="How long to keep inbox items (all sources).">
      <NumberField value={s.retention_days} min={1} max={3650} step={1} onChange={value => patch({ retention_days: value })} width="w-28" ariaLabel="Retention (days)" />
    </Field>}
  </div>
}
