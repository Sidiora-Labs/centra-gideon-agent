import { Select } from '../../shared/ui/forms'
import { useState } from 'react'
import { MessageCircle } from 'lucide-react'
import { api } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { notify } from '../../app/shell/appSdk'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Section } from './settingsUI'

const CHANNELS_KEY = 'settings:approval-channel-list'
const CONFIG_KEY = 'settings:approval-channel-config'

export function ApprovalChannelSection() {
  const channels = useQuery(CHANNELS_KEY, () => api.channels(), { persist: false })
  const config = useQuery(CONFIG_KEY, () => api.gideonConfig(), { persist: false })
  const [busy, setBusy] = useState(false)
  if (channels.error) return <LoadError what="approval channels" error={channels.error} onRetry={channels.refresh} />
  if (config.error) return <LoadError what="approval preference" error={config.error} onRetry={config.refresh} />
  if (!channels.data || !config.data) return <FormSkeleton sections={1} what="approval channels and preference" />

  const selected = String(config.data.agent?.approval_channel || '')
  const options = channels.data.filter(channel => channel.name !== 'webui')
  const save = async (value: string) => {
    setBusy(true)
    try {
      await api.patchConfig('agent.approval_channel', value, String(config.data?.revision || ''))
      await config.refresh()
      notify(value ? `Approval prompts will use ${value} when they did not start on a channel.` : 'Approval prompts will use their originating channel, then the owner default.', 'success')
    } catch (error) {
      notify(`Couldn't save approval channel: ${String((error as Error)?.message || error)}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="Approval channel" icon={MessageCircle} iconTone="muted" hint="Prompts stay in the channel where a conversation started. This choice applies when there is no channel origin; an unavailable explicit choice is not rerouted to another chat.">
      <label className="flex flex-col gap-2" data-type="body-s">
        <span className="text-on-surface">Default channel for approvals</span>
        <Select ariaLabel="Default channel for approvals"
          className="min-h-10 rounded-md border border-outline-variant bg-surface px-3 text-on-surface"
          value={selected}
          disabled={busy}
          onChange={event => void save(event)}
          size="md"
          surface="high"
          options={[{ value: '', label: 'Use the originating channel, then an available owner channel' }, ...(selected && !options.some(channel => channel.name === selected) ? [{ value: selected, label: `${selected} (currently unavailable)` }] : []), ...options.map(channel => ({ value: channel.name, label: `${channel.display_name || channel.name}${channel.connected ? '' : ' (offline)'}` }))]} />
      </label>
    </Section>
  )
}
