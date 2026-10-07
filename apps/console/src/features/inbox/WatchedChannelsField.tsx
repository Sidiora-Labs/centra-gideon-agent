import { useEffect, useState } from 'react'
import { api } from '../../shared/data/api'
import { useStaleWriteGuard } from '../../shared/data/useStaleWriteGuard'
import { StaleWriteNotice } from '../../shared/ui/StaleWriteNotice'
import { Button } from '../../shared/ui/Button'
import { TextInput } from '../../shared/ui/forms'
import { Field } from '../settings/settingsUI'

type WatchedChannelsDocument = { value: string[]; revision: string }
async function readWatchedChannels(): Promise<WatchedChannelsDocument> {
  const config = await api.gideonConfig()
  const revision = config.revisions?.['inbox.watched_channels']
  if (typeof revision !== 'string' || !revision) throw new Error('Inbox channel settings have not been read with a revision.')
  const value = config.inbox?.watched_channels
  return { value: Array.isArray(value) ? value.filter((entry: unknown): entry is string => typeof entry === 'string') : [], revision }
}

export function WatchedChannelsField() {
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

  return <>
    {watchedSources.some(source => source.watches_channels) && <Field label="Channels to read" hint={`Used by ${watchedSources.filter(source => source.watches_channels).map(source => source.display_name || source.name).join(', ')}.`}>
      <div className="grid gap-s">
        <StaleWriteNotice guard={guard} what="Your watched channels" />
        <div className="flex flex-wrap items-end gap-s">
          <div className="min-w-48 flex-1"><TextInput value={channelId} onChange={setChannelId} ariaLabel="Channel ID" placeholder="For example C0123456789" disabled={watchBusy} /></div>
          <Button size="sm" onClick={async () => {
            const value = channelId.trim()
            if (!value || /\s|[\u0000-\u001f\u007f]/.test(value) || value.length > 256) { setWatchError('Enter one channel ID, without spaces, up to 256 characters.'); return }
            if (await changeChannel(value, true)) setChannelId('')
          }} loading={watchBusy} disabled={watchBusy || !channelId.trim()} disabledReason={watchBusy ? 'Wait for the channel update to finish' : !channelId.trim() ? 'Enter a channel ID' : undefined}>Add channel</Button>
        </div>
        <div className="grid gap-xs">
          {(watchedDocument?.value ?? []).map((id, index) => <div key={id} className="flex items-center justify-between gap-s rounded-md bg-surface-container px-m py-s">
            <span data-type="body-s" className="text-on-surface-var">{id}</span>
            <Button ariaLabel={`Remove watched channel ${index + 1} of ${watchedDocument?.value.length ?? 0}`} disabledReason={watchBusy ? 'Wait for the channel update to finish' : undefined} size="xs" variant="ghost" onClick={() => void changeChannel(id, false)} loading={watchBusy} disabled={watchBusy}>Remove</Button>
          </div>)}
        </div>
      </div>
    </Field>}
    {watchError && <p role="alert" data-type="caption" className="text-danger">{watchError}</p>}
  </>
}
