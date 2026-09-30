import { useState } from 'react'
import { UserPlus, XCircle } from 'lucide-react'
import { api, type InboxItem } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { FieldError } from '../../shared/ui/forms'
import { confirm } from '../../shared/ui/dialog'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function SomeoneNewActions({ item, onChanged, onIgnore, busy = false }: {
  item: InboxItem
  onChanged: () => void
  onIgnore: () => void
  busy?: boolean
}) {
  const provider = String(item.refs?.someone_new || '')
  const channel = String(item.refs?.channel_name || provider || 'this channel')
  const sender = item.sender_name || item.sender_id
  const [pairing, setPairing] = useState(false)
  const [error, setError] = useState('')

  if (item.refs?.paired) {
    return <p data-type="body-s" className="text-on-surface-var">Paired. {sender} can talk to your agent on {channel} now.</p>
  }

  const pair = async () => {
    if (!await confirm({
      title: `Pair ${sender}?`,
      body: `${sender} will be able to talk to your agent on ${channel} from their next message. Their held message will not be sent to the agent.`,
      confirmLabel: 'Pair sender',
    })) return
    setPairing(true)
    setError('')
    try {
      await api.pairInboxSender(item.id)
      onChanged()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not pair this sender.')
    } finally {
      setPairing(false)
    }
  }

  const blocked = pairing || busy
  return (
    <div className="flex flex-col gap-m">
      <p data-type="body-s" className="text-on-surface-var">
        {sender} wrote to you on {channel}. Nothing was sent to them and the message did not start an agent turn. Reply below to answer as you, pair them for future messages, or ignore this message.
      </p>
      <div className="flex flex-wrap items-center gap-s">
        <Button size="sm" onClick={() => void pair()} loading={pairing} disabled={blocked} disabledReason={BUSY_REASON}>
          <UserPlus size={14} /> Pair
        </Button>
        <Button size="sm" variant="ghost" onClick={onIgnore} disabled={blocked} disabledReason={BUSY_REASON}>
          <XCircle size={14} /> Ignore
        </Button>
      </div>
      {error && <FieldError>{error}</FieldError>}
    </div>
  )
}
