import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { InboxDetail } from './InboxDetail'
import type { InboxItem } from '../../shared/data/api'

describe('sent Inbox replies', () => {
  it('shows the sent text and timestamp, with a reopen action instead of another send', () => {
    const item: InboxItem = {
      id: 'message_123456789012345678901234_1700000000',
      channel: 'room-a',
      channel_name: 'Room A',
      thread_ts: 'thread-root',
      message: 'The incoming message',
      sender_id: 'sender',
      sender_name: 'Sender',
      classification: 'needs_reply',
      confidence: 'high',
      status: 'sent',
      source: 'slack',
      can_reply: true,
      draft: 'The response already sent',
      replied_at: 1700000100,
    }
    render(<InboxDetail
      item={item}
      onChanged={() => window.dispatchEvent(new Event('inbox-changed'))}
      navigate={path => window.history.pushState({}, '', path)}
    />)
    expect(screen.getByRole('heading', { name: 'Your reply' })).toBeInTheDocument()
    expect(screen.getByText('The response already sent')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reopen/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /send reply/i })).toBeNull()
  })
})
