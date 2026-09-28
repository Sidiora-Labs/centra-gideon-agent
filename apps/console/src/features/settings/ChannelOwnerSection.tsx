import { useState } from 'react'
import { KeyRound, UserRoundCheck } from 'lucide-react'
import { api } from '../../shared/data/api'
import type { ChannelRuntime } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { EmptyState, FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { PanelHeader, Section } from './settingsUI'

const CHANNELS_KEY = 'settings:channel-owner-channels'
const labelFor = (channel: ChannelRuntime) => channel.display_name || channel.name

export function ChannelOwnerSection() {
  const { data, error, refresh } = useQuery(CHANNELS_KEY, () => api.channels(), { persist: false })
  if (!data && error) return <LoadError what="channel owner pairing" error={error} onRetry={refresh} />
  if (!data) return <FormSkeleton sections={1} what="channel owner pairing" />
  const eligible = data.filter(channel => channel.name !== 'webui' && channel.capabilities?.owner_pairing === true)

  return (
    <div className="space-y-2xl">
      <PanelHeader
        title="Channel owner"
        hint="Pair the direct-message identity you own on each channel. A pairing code is single-use and expires after ten minutes."
      />
      {eligible.length === 0 ? (
        <EmptyState icon={UserRoundCheck} title="No owner-pairing channel is available" hint="Connect a supported messaging channel first." />
      ) : eligible.map(channel => <OwnerRow key={channel.name} channel={channel} />)}
    </div>
  )
}

function OwnerRow({ channel }: { channel: ChannelRuntime }) {
  const queryKey = `settings:channel-owner:${channel.name}`
  const { data, error, refresh } = useQuery(queryKey, () => api.channelOwner(channel.name), { persist: false })
  const [busy, setBusy] = useState(false)
  const [code, setCode] = useState('')

  const issue = async () => {
    setBusy(true)
    setCode('')
    try {
      const result = await api.createChannelOwnerPairing(channel.name)
      setCode(result.code)
      notify(`Pairing code ready for ${labelFor(channel)}.`, 'success')
      refresh()
    } catch (cause) {
      notify(`Couldn't create a pairing code: ${String((cause as Error)?.message || cause)}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    setBusy(true)
    try {
      await api.cancelChannelOwnerPairing(channel.name)
      setCode('')
      notify(`Pairing code cancelled for ${labelFor(channel)}.`, 'success')
      refresh()
    } catch (cause) {
      notify(`Couldn't cancel the pairing code: ${String((cause as Error)?.message || cause)}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section
      title={labelFor(channel)}
      icon={UserRoundCheck}
      iconTone="muted"
      hint={data?.owner_configured ? 'An owner identity is configured for this channel.' : 'No owner identity is configured for this channel yet.'}
    >
      {!data && error ? <LoadError what={`${labelFor(channel)} owner state`} error={error} onRetry={refresh} /> : null}
      {data?.pairing.active ? (
        <div className="space-y-3">
          <p data-type="body-s" className="text-on-surface-low">
            A one-use owner code is active until {new Date(data.pairing.expires_at).toLocaleTimeString()}.
            Send it to the channel from your direct-message account. {data.pairing.attempts_left} attempts remain.
          </p>
          <Button size="xs" variant="danger" onClick={cancel} loading={busy} ariaLabel={`Cancel ${labelFor(channel)} owner pairing`}>
            Cancel code
          </Button>
        </div>
      ) : (
        <Button size="xs" onClick={issue} loading={busy} ariaLabel={`Create ${labelFor(channel)} owner pairing code`}>
          <KeyRound size={15} aria-hidden="true" /> Create owner code
        </Button>
      )}
      {code ? (
        <div role="status" aria-live="polite" className="mt-3 rounded-md border border-outline-low p-3">
          <p data-type="body-s" className="text-on-surface">Send this code from your direct message with {labelFor(channel)}:</p>
          <code className="mt-2 block select-all text-lg font-semibold tracking-widest text-on-surface">{code}</code>
          <p data-type="caption" className="mt-2 text-on-surface-low">It is shown once and expires in ten minutes.</p>
        </div>
      ) : null}
    </Section>
  )
}
