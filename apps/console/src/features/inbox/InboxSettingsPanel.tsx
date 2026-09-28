import { useEffect, useState } from 'react'
import { InboxConfigBoundary } from './InboxConfigBoundary'
import { useInboxSettingsState } from './inboxSettingsState'
import { Loading, LoadError } from '../../shared/ui/ListScaffold'
import { Row, Field, Toggle, SavedToast } from '../settings/settingsUI'
import { NumberField, TextInput } from '../../shared/ui/forms'
import { TextLink } from '../../shared/ui/TextLink'
import { api } from '../../shared/data/api'
import { useStaleWriteGuard } from '../../shared/data/useStaleWriteGuard'
import { StaleWriteNotice } from '../../shared/ui/StaleWriteNotice'
import { Button } from '../../shared/ui/Button'

type WatchedChannelsDocument = { value: string[]; revision: string }
async function readWatchedChannels(): Promise<WatchedChannelsDocument> {
  const config = await api.gideonConfig()
  const revision = config.revisions?.['inbox.watched_channels']
  if (typeof revision !== 'string' || !revision) throw new Error('Inbox channel settings have not been read with a revision.')
  const value = config.inbox?.watched_channels
  return { value: Array.isArray(value) ? value.filter((entry: unknown): entry is string => typeof entry === 'string') : [], revision }
}

export function InboxSettingsPanel() {
  const { s, saved, cfgErr, cfgLoading, retryConfig, loadErr, load, engagementOn, sourcesOn, patch, setEngagement, setSources } = useInboxSettingsState()
  const [watchedDocument, setWatchedDocument] = useState<WatchedChannelsDocument | null>(null)
  const [watchedSources, setWatchedSources] = useState<Array<{ name: string; display_name?: string; watches_channels?: boolean }>>([])
  const [channelId, setChannelId] = useState('')
  const [watchError, setWatchError] = useState('')
  const [watchBusy, setWatchBusy] = useState(false)
  const refreshWatchedDocument = () => {
    void readWatchedChannels().then(setWatchedDocument).catch(error => setWatchError(String((error as Error)?.message || error)))
  }
  const guard = useStaleWriteGuard<string[]>({
    read: readWatchedChannels,
    write: (next, revision) => api.patchConfig('inbox.watched_channels', next, revision),
    onSaved: refreshWatchedDocument,
    onDiscard: refreshWatchedDocument,
  })
  useEffect(() => {
    let current = true
    Promise.all([readWatchedChannels(), api.inboxProviders()]).then(([document, providers]) => {
      if (current) { setWatchedDocument(document); setWatchedSources(providers) }
    }).catch(error => { if (current) setWatchError(String((error as Error)?.message || error)) })
    return () => { current = false }
  }, [])

  const changeChannel = async (id: string, adding: boolean) => {
    setWatchError('')
    if (!watchedDocument) { setWatchError('Inbox channel settings are still loading.'); return }
    setWatchBusy(true)
    try {
      const operation = (base: string[]) => {
        const next = adding
          ? base.includes(id) ? null : [...base, id]
          : base.includes(id) ? base.filter(value => value !== id) : null
        return next
      }
      const saved = await guard.apply(watchedDocument, operation)
      return saved
    } catch (error) { setWatchError(String((error as Error)?.message || error)) }
    finally { setWatchBusy(false) }
  }

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
    {watchedSources.some(source => source.watches_channels) && <Field label="Channels to read" hint={`Used by ${watchedSources.filter(source => source.watches_channels).map(source => source.display_name || source.name).join(', ')}.`}>
      <div className="grid gap-s">
        <StaleWriteNotice guard={guard} what="Your watched channels" />
        <div className="flex flex-wrap items-end gap-s">
          <div className="min-w-48 flex-1"><TextInput value={channelId} onChange={setChannelId} ariaLabel="Channel ID" placeholder="For example C0123456789" disabled={watchBusy} /></div>
          <Button size="sm" onClick={async () => {
            const value = channelId.trim()
            if (!value || /\s|[\u0000-\u001f\u007f]/.test(value) || value.length > 256) { setWatchError('Enter one channel ID, without spaces, up to 256 characters.'); return }
            if (await changeChannel(value, true)) setChannelId('')
          }} loading={watchBusy} disabled={watchBusy || !channelId.trim()}>Add channel</Button>
        </div>
        <div className="grid gap-xs">
          {(watchedDocument?.value ?? []).map(id => <div key={id} className="flex items-center justify-between gap-s rounded-md bg-surface-container px-m py-s">
            <span data-type="body-s" className="text-on-surface-var">{id}</span>
            <Button size="xs" variant="ghost" onClick={() => void changeChannel(id, false)} loading={watchBusy} disabled={watchBusy}>Remove</Button>
          </div>)}
        </div>
        {watchError && <p role="alert" data-type="caption" className="text-danger">{watchError}</p>}
      </div>
    </Field>}
  </div>
}
