import { useEffect, useState } from 'react'
import { api, type InboxSettings } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, Row, Toggle, SavedToast } from './settingsUI'
import { TriageRulesCard } from './TriageRulesCard'
import { NumberField } from '../../shared/ui/forms'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { InboxConfigBoundary } from '../inbox/InboxConfigBoundary'
import { notify } from '../../app/shell/appSdk'
import { TextLink } from '../../shared/ui/TextLink'

export function InboxSettingsPanel() {
  const [s, setS] = useState<InboxSettings | null>(null)
  const [saved, setSaved] = useState(false)
  const [engagementOn, setEngagementOn] = useState<boolean | null>(null)
  const [sourcesOn, setSourcesOn] = useState<boolean | null>(null)
  const [triageOn, setTriageOn] = useState<boolean | null>(null)
  const [autoExecOn, setAutoExecOn] = useState<boolean | null>(null)
  const [cfgErr, setCfgErr] = useState('')

  const { data, error: loadErr, refresh } = useQuery('settings:inbox', () => api.inboxSettings(), { persist: true })
  const { data: config, error: configError, revalidating: cfgLoading, refresh: refreshConfig } = useQuery('settings:inbox-config', () => api.gideonConfig())
  useEffect(() => { if (data) setS(data) }, [data])

  useEffect(() => {
    if (cfgLoading) {
      setCfgErr('')
    } else if (configError) {
      setCfgErr(String((configError as Error)?.message || configError))
    } else if (config) {
      setEngagementOn(Boolean(config?.inbox?.engagement_ranking_enabled))
      setSourcesOn(Boolean(config?.inbox?.enabled))
      setTriageOn(Boolean(config?.proactive?.triage_enabled))
      setAutoExecOn(Boolean(config?.proactive?.auto_execute_enabled))
      setCfgErr('')
    }
  }, [config, configError, cfgLoading])

  const patch = (p: Partial<InboxSettings>) => {
    const prev = s
    setS((cur) => cur && { ...cur, ...p })
    api.saveInboxSettings(p)
      .then(() => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) })
      .catch((e) => {
        setS(prev)
        notify(`Couldn't save your inbox settings: ${String((e as Error)?.message || e)}`, 'error')
      })
  }

  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) }

  const setSources = (v: boolean) => {
    setSourcesOn(v)
    api.patchConfig('inbox.enabled', v)
      .then(() => api.restartInbox())
      .then(() => { refreshConfig(); flash() })
      .catch(() => setSourcesOn(!v))
  }

  const setTriage = (v: boolean) => {
    setTriageOn(v)
    api.patchConfig('proactive.triage_enabled', v)
      .then(() => api.proactiveInstall().catch(() => undefined))
      .then(() => { refreshConfig(); flash() })
      .catch((e) => { setTriageOn(!v); notify(`Couldn't change that: ${String((e as Error)?.message || e)}`, 'error') })
  }

  const setAutoExec = (v: boolean) => {
    setAutoExecOn(v)
    api.patchConfig('proactive.auto_execute_enabled', v)
      .then(() => { refreshConfig(); flash() })
      .catch((e) => { setAutoExecOn(!v); notify(`Couldn't change that: ${String((e as Error)?.message || e)}`, 'error') })
  }

  const setEngagement = (v: boolean) => {
    setEngagementOn(v)
    api.patchConfig('inbox.engagement_ranking_enabled', v)
      .then(() => { refreshConfig(); flash() })
      .catch(() => setEngagementOn(!v))
  }

  if (!data && loadErr) return <LoadError what="inbox settings" error={loadErr} onRetry={refresh} />
  if (!data || !s) return <FormSkeleton sections={2} what="inbox settings" />
  return (
    <div>
      <PanelHeader title="Inbox" hint="What gets flagged in the unified inbox, and how long items are kept." />
      <div className="mb-l flex justify-end"><SavedToast show={saved} /></div>

      {
}
      <Section title="Alerts" hint="Keyword and name-mention alerts are now per-notification-kind.">
        <Row label="Where to configure" hint="One place for every kind of notification, not just inbox items.">
          <TextLink href="#/settings/notifications" ink="emphasis" size="sm">Open notification rules</TextLink>
        </Row>
      </Section>

      {
}
      <Section title="Collection" hint="What the inbox gathers, and how it is ordered.">
        <InboxConfigBoundary cfgErr={cfgErr} loading={cfgLoading || sourcesOn === null || engagementOn === null} onRetry={refreshConfig}>
        <Row label="Poll message sources"
          hint="Collect messages from connected poll sources (filesystem drops; channel apps). Agents can always post here directly.">
          <Toggle on={!!sourcesOn} onChange={setSources} label="Poll message sources" disabled={sourcesOn === null} />
        </Row>
        <Row label="Engagement ranking"
          hint="Rank the inbox by how much you engage with each channel/sender (favorites, opens, replies boost; dismisses lower) on top of recency. Off = pure newest-first.">
          <Toggle on={!!engagementOn} onChange={setEngagement} label="Engagement ranking" disabled={engagementOn === null} />
        </Row>
        </InboxConfigBoundary>
      </Section>

      {
}
      <Section title="Proactive triage" hint="One scheduled digest of what accumulated, with proposals you answer. Off by default; nothing is collected or spent while it is off.">
        <InboxConfigBoundary cfgErr={cfgErr} loading={cfgLoading || triageOn === null || autoExecOn === null} onRetry={refreshConfig}>
        <Row label="Morning triage digest" hint="Collect, filter and propose on a schedule. Turning this off retires the schedule and keeps every rule you taught — turning it back on is lossless.">
          <Toggle on={!!triageOn} onChange={setTriage} label="Morning triage digest"
            disabled={triageOn === null}
            disabledReason="Still reading your configuration — this switch appears once it loads." />
        </Row>
        <Row label="Auto-execute the trivial tier" hint="Let the digest perform reversible inbox actions (archive, mark read, mute) on its own, inside your daily budget and per-run cap. Every one is a ledger row with a one-click undo.">
          <Toggle on={!!autoExecOn} onChange={setAutoExec} label="Auto-execute the trivial tier"
            disabled={autoExecOn === null || !triageOn}
            disabledReason={autoExecOn === null
              ? 'Still reading your configuration — this switch appears once it loads.'
              : 'Turn the Morning triage digest on first — there is nothing to auto-execute without it.'} />
        </Row>
        </InboxConfigBoundary>
      </Section>

      <TriageRulesCard />

      <Section title="Retention" hint="Automatically clean up old inbox items.">
        <Row label="Auto-cleanup" hint="Remove items past their retention window.">
          <Toggle on={s.auto_cleanup_enabled} onChange={(v) => patch({ auto_cleanup_enabled: v })} label="Auto-cleanup" />
        </Row>
        {s.auto_cleanup_enabled && (
          <Row label="Retention" hint="How long to keep inbox items (all sources).">
            <div className="flex items-center gap-2">
              <NumberField value={s.retention_days} min={1} max={3650} onChange={(v) => patch({ retention_days: v })} width="w-20" ariaLabel="Retention (days)" />
              <span data-type="caption" className="text-on-surface-low">days</span>
            </div>
          </Row>
        )}
      </Section>
    </div>
  )
}
