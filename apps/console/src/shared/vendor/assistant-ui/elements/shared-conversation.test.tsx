import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SharedConversation } from './shared-conversation'

const turns = [{ id: 'u1', role: 'user' as const, text: 'What changed?' },
  { id: 'a1', role: 'assistant' as const, text: 'Q3 report updated.' }]

describe('shared conversation labels', () => {
  it('keeps donor labels and action by default', () => {
    let continued = 0
    render(<SharedConversation title="Report" sharedBy="Alex" sharedAt="today" turns={turns}
      onContinue={() => { continued += 1 }} />)
    expect(screen.getByText('shared by Alex · today')).toBeInTheDocument()
    expect(screen.getByText('read only')).toBeInTheDocument()
    expect(screen.getByText('Q3 report updated.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Continue in your own chat' }))
    expect(continued).toBe(1)
  })

  it('renders supplied localized labels from real owner metadata', () => {
    render(<SharedConversation title="Bericht" sharedBy="Alex" sharedAt="26.09.2026" turns={turns}
      labels={{ sharedBy: (by, at) => `Geteilt von ${by} · ${at}`, readOnly: 'Nur lesen', continue: 'Im Chat fortsetzen' }} />)
    expect(screen.getByText('Geteilt von Alex · 26.09.2026')).toBeInTheDocument()
    expect(screen.getByText('Nur lesen')).toBeInTheDocument()
    expect(screen.queryByText('shared by Alex · 26.09.2026')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Im Chat fortsetzen' })).toBeNull()
  })

  it('omits unknown owner or time instead of suggesting public sharing', () => {
    render(<SharedConversation title="Private archive" turns={turns} />)
    expect(screen.queryByText(/shared by/)).toBeNull()
    expect(screen.getByText('read only')).toBeInTheDocument()
  })
})
