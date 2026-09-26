import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { assistantTurn, userTurn, type ChatTurn } from './chatTypes'
import { ConversationHistoryView } from './auiConversationViews'

const answer = (text: string, ts?: string): ChatTurn => ({ ...assistantTurn(text), ts })

describe('selected conversation history alternate view', () => {
  it('defaults to a single chronological day view and labels only real zoned timestamps in UTC', () => {
    const turns: ChatTurn[] = [
      userTurn('Late question', '2026-09-26T23:45:00+02:00'),
      answer('Same-day answer', '2026-09-26T22:15:00Z'),
      userTurn('Undated follow-up'),
      answer('Next-day answer', '2026-09-27T01:05:00Z'),
    ]
    const { container } = render(<ConversationHistoryView turns={turns} />)
    const view = within(container.querySelector('[aria-label="Conversation history views"]') as HTMLElement)
    expect(view.getByRole('button', { name: 'Days' })).toHaveAttribute('aria-pressed', 'true')
    expect(container.querySelectorAll('[data-slot="day-separator"]')).toHaveLength(2)
    expect(container.querySelectorAll('[data-slot="speaker-identity"]')).toHaveLength(1)
    expect(screen.getByText('2026-09-26 UTC')).toBeInTheDocument()
    expect(screen.getByText('21:45 UTC')).toBeInTheDocument()
    expect(screen.getByText('22:15 UTC')).toBeInTheDocument()
    expect(screen.getByText('2026-09-27 UTC')).toBeInTheDocument()
    expect(screen.getByText('01:05 UTC')).toBeInTheDocument()
    expect(screen.getByText('Undated follow-up')).toBeInTheDocument()
    expect(screen.queryByText('00:00 UTC')).toBeNull()
    const text = container.textContent || ''
    expect(text.indexOf('Late question')).toBeLessThan(text.indexOf('Same-day answer'))
    expect(text.indexOf('Same-day answer')).toBeLessThan(text.indexOf('Undated follow-up'))
    expect(text.indexOf('Undated follow-up')).toBeLessThan(text.indexOf('Next-day answer'))
    expect(container.querySelector('[data-slot="message-pair"]')).toBeNull()
  })

  it('keeps invalid, timezone-free, and absent timestamps undated without inventing a separator', () => {
    const turns: ChatTurn[] = [
      userTurn('No timestamp'),
      answer('Invalid date', 'not-a-date'),
      userTurn('No zone', '2026-09-27T10:30:00'),
      answer('Impossible zone', '2026-09-27T10:30:00+99:99'),
    ]
    const { container } = render(<ConversationHistoryView turns={turns} />)
    expect(container.querySelector('[data-slot="day-separator"]')).toBeNull()
    expect(container.querySelector('[data-slot="speaker-identity"]')).toBeInTheDocument()
    expect(screen.getAllByText('You')).toHaveLength(2)
    expect(screen.getAllByText('Gideon')).toHaveLength(2)
    expect(screen.queryByText(/UTC/)).toBeNull()
    for (const text of ['No timestamp', 'Invalid date', 'No zone', 'Impossible zone']) {
      expect(screen.getByText(text)).toBeInTheDocument()
    }
  })

  it('switches to actual adjacent user-assistant pairs, leaving interrupted turns as singles', () => {
    const turns: ChatTurn[] = [
      userTurn('First question'), answer('First answer'),
      userTurn('Second question'), assistantTurn(''), answer('Answer after an empty assistant turn'),
    ]
    const { container } = render(<ConversationHistoryView turns={turns} />)
    fireEvent.click(screen.getByRole('button', { name: 'Pairs' }))
    expect(screen.getByRole('button', { name: 'Pairs' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Days' })).toHaveAttribute('aria-pressed', 'false')
    const pair = within(container.querySelector('[data-slot="message-pair"]') as HTMLElement)
    expect(pair.getByText('First question')).toBeInTheDocument()
    expect(pair.getByText('First answer')).toBeInTheDocument()
    expect(pair.queryByRole('button', { name: 'Copy response' })).toBeNull()
    expect(pair.queryByRole('button', { name: 'Regenerate response' })).toBeNull()
    expect(container.querySelectorAll('[data-slot="message-pair"]')).toHaveLength(1)
    expect(screen.getByText('Second question')).toBeInTheDocument()
    expect(screen.getByText('Answer after an empty assistant turn')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-slot="speaker-identity"]')).toHaveLength(2)
    expect(container.querySelector('[data-slot="day-separator"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Days' }))
    expect(container.querySelector('[data-slot="message-pair"]')).toBeNull()
  })

  it('renders selected text once with authoritative You/Gideon roles in speaker mode', () => {
    const selected: ChatTurn = { ...answer('Selected response', '2026-09-26T10:00:00Z'),
      variantCount: 2, variantIdx: 1 }
    const turns: ChatTurn[] = [userTurn('Read this'), selected, assistantTurn('')]
    const { container } = render(<ConversationHistoryView turns={turns} />)
    fireEvent.click(screen.getByRole('button', { name: 'Speakers' }))
    const donor = within(container.querySelector('[data-slot="speaker-identity"]') as HTMLElement)
    expect(donor.getByText('You')).toBeInTheDocument()
    expect(donor.getByText('Gideon')).toBeInTheDocument()
    expect(donor.getByText('Read this')).toBeInTheDocument()
    expect(donor.getByText('Selected response')).toBeInTheDocument()
    expect(donor.getByText('2026-09-26 UTC · 10:00 UTC')).toBeInTheDocument()
    expect(donor.queryByText('tool')).toBeNull()
    expect(donor.queryByText('subagent')).toBeNull()
    expect(container.querySelectorAll('[data-slot="speaker-identity"] > div')).toHaveLength(2)
    expect(container.querySelector('[data-slot="day-separator"]')).toBeNull()
    expect(container.querySelector('[data-slot="message-pair"]')).toBeNull()
    expect(screen.queryByRole('button', { name: /copy|regenerate|send/i })).toBeNull()
  })

  it('shows an honest empty text state across view changes without fabricating messages', () => {
    const { container, rerender } = render(<ConversationHistoryView turns={[]} />)
    expect(screen.getByText('No text messages in this chat.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Pairs' }))
    expect(screen.getByText('No text messages in this chat.')).toBeInTheDocument()
    rerender(<ConversationHistoryView turns={[assistantTurn('')]} />)
    expect(screen.getByText('No text messages in this chat.')).toBeInTheDocument()
    expect(container.querySelector('[data-slot="message-pair"], [data-slot="day-separator"], [data-slot="speaker-identity"]')).toBeNull()
  })

  it('keeps all three native view controls focusable and touchable in the selected panel', () => {
    render(<ConversationHistoryView turns={[userTurn('Question'), answer('Answer')]} />)
    const buttons = ['Pairs', 'Days', 'Speakers'].map((name) => screen.getByRole('button', { name }))
    expect(buttons.every((button) => button.className.includes('min-h-10'))).toBe(true)
    buttons[2].focus()
    expect(buttons[2]).toHaveFocus()
    fireEvent.click(buttons[2])
    expect(buttons[2]).toHaveAttribute('aria-pressed', 'true')
  })

  it('accepts hosted labels for controls, role identities, empty state, and accessible names', () => {
    const labels = { pairs: 'Paare', days: 'Tage', speakers: 'Sprecher', you: 'Du', gideon: 'Gideon DE',
      empty: 'Keine Textnachrichten.', history: 'Gesprächsverlauf', view: 'Ansicht', pair: 'Du und Gideon DE' }
    const { container, rerender } = render(<ConversationHistoryView
      turns={[userTurn('Frage'), answer('Antwort')]} labels={labels} />)
    expect(screen.getByRole('region', { name: 'Gesprächsverlauf' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Ansicht' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tage' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Du')).toBeInTheDocument()
    expect(screen.getByText('Gideon DE')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Paare' }))
    expect(container.querySelector('[data-slot="message-pair"]')).toHaveAttribute('aria-label', 'Du und Gideon DE')
    fireEvent.click(screen.getByRole('button', { name: 'Sprecher' }))
    expect(screen.getByText('Du')).toBeInTheDocument()
    rerender(<ConversationHistoryView turns={[]} labels={labels} />)
    expect(screen.getByText('Keine Textnachrichten.')).toBeInTheDocument()
    expect(screen.queryByText('No text messages in this chat.')).toBeNull()
  })
})
