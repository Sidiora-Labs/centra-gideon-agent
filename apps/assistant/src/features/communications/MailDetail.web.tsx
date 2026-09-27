import React from 'react'
import { providerItemKey, type CommunicationItem, type MirrorAccount, type MirrorMessage, type OutboundDraftItem } from './types'

export type MailDetailProps = Readonly<{
  account: MirrorAccount
  item: CommunicationItem<MirrorMessage> | OutboundDraftItem
  thread: readonly CommunicationItem<MirrorMessage>[]
  onBack: () => void
  onReply: (message: MirrorMessage) => void
  onSetReadState: (item: CommunicationItem<MirrorMessage>, isRead: boolean) => void
  readBusy: boolean
  onEditDraft: (draft: OutboundDraftItem) => void
  onReviewDraft: (draft: OutboundDraftItem) => void
}>

const card: React.CSSProperties = { background: 'var(--mail-card)', border: '1px solid var(--mail-line)', borderRadius: 16, padding: 20, color: 'var(--mail-text)' }
const muted: React.CSSProperties = { color: 'var(--mail-muted)', fontSize: 13 }

function dateLabel(value: string | null): string {
  if (!value) return 'Date unavailable'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function MailDetail({ account, item, thread, onBack, onReply, onSetReadState, readBusy, onEditDraft, onReviewDraft }: MailDetailProps) {
  const draft = 'content_sha256' in item.value
  if (draft) {
    const outbound = item as OutboundDraftItem
    const value = outbound.value
    return <section aria-labelledby="mail-detail-title" style={{ display: 'grid', gap: 16 }}>
      <button type="button" onClick={onBack} style={{ justifySelf: 'start' }}>← Back to messages</button>
      <article style={card}>
        <p style={muted}>Draft · {account.name} · revision {value.revision}</p>
        <h2 id="mail-detail-title">{value.subject || '(No subject)'}</h2>
        <p><strong>From:</strong> {value.sender}</p>
        <p><strong>To:</strong> {value.to.join(', ')}</p>
        <p style={{ whiteSpace: 'pre-wrap' }}>{value.body}</p>
        <h3>Attachments</h3>
        {value.attachments.length ? <ul>{value.attachments.map(file => <li key={`${file.artifact_id}:${file.version}`}>{file.name} <span style={muted}>({file.mime}, {file.size} bytes)</span></li>)}</ul> : <p style={muted}>No attachments</p>}
        <p role="status">Delivery: {value.delivery.replaceAll('_', ' ')} · {value.state}</p>
        {value.state === 'uncertain' && <p role="alert">The provider result is uncertain. Reconcile this same draft before considering another send.</p>}
        {['draft', 'approved'].includes(value.state) && <button type="button" onClick={() => onEditDraft(outbound)}>Edit draft</button>}
        <button type="button" onClick={() => onReviewDraft(outbound)} disabled={!outbound.allowedActions.includes('review') && !outbound.allowedActions.includes('send')}>
          Review this draft
        </button>
      </article>
    </section>
  }

  const messageItem = item as CommunicationItem<MirrorMessage>
  const message = messageItem.value
  const orderedThread = [...thread].sort((a, b) => (a.value.occurred_at ?? '').localeCompare(b.value.occurred_at ?? ''))
  return <section aria-labelledby="mail-detail-title" style={{ display: 'grid', gap: 16 }}>
    <button type="button" onClick={onBack} style={{ justifySelf: 'start' }}>← Back to messages</button>
    <article style={card}>
      <p style={muted}>{account.name} · {messageItem.freshness === 'stale' ? 'Stale snapshot' : messageItem.freshness === 'imported' ? 'Imported snapshot' : 'Current snapshot'}</p>
      <h2 id="mail-detail-title">{message.subject || '(No subject)'}</h2>
      <p><strong>From:</strong> {message.sender.join(', ') || 'Sender unavailable'}</p>
      <p><strong>To:</strong> {message.recipients.join(', ') || 'Recipients unavailable'}</p>
      <p style={muted}>{dateLabel(message.occurred_at)} · Thread {message.thread_id || message.external_id}</p>
      <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.65 }}>{message.body || '(No message body)'}</p>
      <h3>Attachments</h3>
      {message.attachments.length ? <ul>{message.attachments.map((file, index) => <li key={`${file.artifact_id ?? file.filename}:${index}`}>
        {file.filename} <span style={muted}>({file.content_type}, {file.size} bytes{file.state === 'unsupported_content' ? ', content unavailable' : ''})</span>
      </li>)}</ul> : <p style={muted}>No attachments</p>}
      <p style={muted}>Read state is tracked locally in Gideon; the mailbox provider is not modified.</p>
      <button type="button" disabled={readBusy || messageItem.freshness === 'stale'} onClick={() => onSetReadState(messageItem, !message.is_read)}>
        {readBusy ? 'Updating read state…' : message.is_read ? 'Mark unread in Gideon' : 'Mark read in Gideon'}
      </button>
    </article>
    {orderedThread.length > 1 && <section aria-label="Thread history" style={{ display: 'grid', gap: 10 }}>
      <h3>Thread history · {orderedThread.length} messages</h3>
      {orderedThread.map(part => <article key={providerItemKey(part.identity)} style={{ ...card, padding: 16 }}>
        <strong>{part.value.sender.join(', ') || 'Sender unavailable'}</strong>
        <span style={{ ...muted, marginInlineStart: 10 }}>{dateLabel(part.value.occurred_at)}</span>
        <p style={{ marginBottom: 0 }}>{part.value.subject || '(No subject)'}</p>
        <p style={{ whiteSpace: 'pre-wrap' }}>{part.value.body}</p>
      </article>)}
    </section>}
      <button type="button" onClick={() => onReply(message)} disabled={account.kind !== 'imap' || messageItem.readiness === 'unavailable' || messageItem.readiness === 'expired'}>
        {account.kind === 'imap' ? 'Reply with a governed draft' : 'Reply unavailable for this imported mailbox'}
      </button>
  </section>
}
