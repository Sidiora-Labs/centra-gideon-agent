import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Sunrise, Moon } from 'lucide-react'
import { PresetCard, PresetEmptyState, type PresetDef } from './PresetEmptyState'


interface Prefill { cron: string; label: string }

const PRESETS: PresetDef<Prefill>[] = [
  {
    id: 'morning-briefing',
    icon: Sunrise,
    title: 'Morning briefing',
    summary: 'Every day · 8:00 AM',
    description: 'An agent writes you a short start-of-day briefing.',
    prefill: { cron: '0 8 * * *', label: 'briefing' },
  },
  {
    id: 'nightly-check',
    icon: Moon,
    title: 'Nightly check',
    summary: 'Every day · 11:00 PM',
    description: 'Looks for anything left broken.',
    prefill: { cron: '0 23 * * *', label: 'nightly' },
  },
]

const mount = (onPick = vi.fn(), footer?: React.ReactNode) => {
  const r = render(
    <PresetEmptyState title="No triggers" hint="Start from one of these." presets={PRESETS} onPick={onPick} footer={footer} />,
  )
  return { ...r, onPick }
}

describe('PresetEmptyState', () => {
  it('renders one card per preset, with the four things a preset is', () => {
    mount()
    expect(screen.getAllByRole('button')).toHaveLength(PRESETS.length)
    expect(screen.getByText('Morning briefing')).toBeInTheDocument()
    expect(screen.getByText('Every day · 8:00 AM')).toBeInTheDocument()
    expect(screen.getByText('An agent writes you a short start-of-day briefing.')).toBeInTheDocument()
    expect(document.querySelectorAll('svg[aria-hidden="true"]').length).toBeGreaterThanOrEqual(2)
  })

  it('keeps the headline + hint of an ordinary empty state', () => {
    mount()
    expect(screen.getByRole('heading', { name: 'No triggers' })).toBeInTheDocument()
    expect(screen.getByText('Start from one of these.')).toBeInTheDocument()
  })

  it('hands the picked preset\'s PREFILL back to the caller — not just its identity', async () => {
    const { onPick } = mount()
    await userEvent.click(screen.getByRole('button', { name: /Morning briefing/ }))
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick).toHaveBeenCalledWith({ cron: '0 8 * * *', label: 'briefing' })
  })

  it('names each card by what it will do, not by its own prose', () => {
    mount()
    expect(screen.getByRole('button', { name: 'Morning briefing — Every day · 8:00 AM' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nightly check — Every day · 11:00 PM' })).toBeInTheDocument()
  })

  it('is reachable by Tab and activatable by Enter and by Space', async () => {
    const { onPick } = mount()
    const cards = screen.getAllByRole('button')
    await userEvent.tab()
    expect(cards[0]).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(onPick).toHaveBeenCalledWith(PRESETS[0].prefill)

    await userEvent.tab()
    expect(cards[1]).toHaveFocus()
    await userEvent.keyboard(' ')
    expect(onPick).toHaveBeenLastCalledWith(PRESETS[1].prefill)
    expect(onPick).toHaveBeenCalledTimes(2)
  })

  it('shows a visible focus ring on the focused card', () => {
    mount()
    const card = screen.getAllByRole('button')[0]
    const cls = (card.className || '').split(/\s+/)
    expect(cls).toContain('focus-visible:ring-2')
    expect(cls).toContain('focus-visible:ring-inset')
    expect(cls).toContain('focus-visible:ring-primary')
    expect(cls).toContain('focus-visible:outline-none')
  })

  it('puts no interactive element inside a card', () => {
    mount()
    for (const card of screen.getAllByRole('button')) {
      expect(card.querySelectorAll('button, a[href], input, select, textarea, [tabindex]')).toHaveLength(0)
    }
  })

  it('renders the expert blank path in the footer slot, outside the cards', () => {
    const blank = vi.fn()
    mount(vi.fn(), <button type="button" onClick={blank}>Start from scratch</button>)
    const escape = screen.getByRole('button', { name: 'Start from scratch' })
    expect(escape).toBeInTheDocument()
    for (const card of screen.getAllByRole('button', { name: /—/ }))
      expect(card.contains(escape)).toBe(false)
  })
})

describe('PresetCard', () => {
  it('works standalone — the grid is not part of its contract', async () => {
    const onPick = vi.fn()
    render(
      <PresetCard icon={Sunrise} title="Weekly digest" summary="Every Monday · 9:00 AM"
        description="What moved and what stalled." prefill={{ n: 7 }} onPick={onPick} />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Weekly digest — Every Monday · 9:00 AM' }))
    expect(onPick).toHaveBeenCalledWith({ n: 7 })
  })
})
