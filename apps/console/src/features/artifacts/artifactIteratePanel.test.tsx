import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ArtifactIteratePanel, ITERATE_PENDING } from './ArtifactIteratePanel'


const investigate = vi.fn()
vi.mock('../../shared/data/api', async (orig) => {
  const real = await orig<typeof import('../../shared/data/api')>()
  return { ...real, api: { ...real.api, investigate: (body: unknown) => investigate(body) } }
})

beforeEach(() => { investigate.mockReset() })

function embedSrc(): string {
  const f = screen.queryByTitle('Gideon chat')
  return f ? (f as HTMLIFrameElement).getAttribute('src') ?? '' : ''
}

function mount(session = ITERATE_PENDING, onSession = vi.fn(), onClose = vi.fn()) {
  const view = render(<ArtifactIteratePanel slug="revenue-widget" name="Revenue widget"
    session={session} onSession={onSession} onClose={onClose} />)
  return { view, onSession, onClose }
}

describe('the iterate panel stages its session through the investigate resolver', () => {
  it('asks the resolver for an artifact session and embeds it, seeded with the prompt', async () => {
    investigate.mockResolvedValue({
      session_key: 'sess-42',
      context: { opening_prompt: 'Iterate on artifact `revenue-widget`.' },
    })
    const { onSession } = mount()

    await waitFor(() => expect(embedSrc()).not.toBe(''))
    expect(investigate).toHaveBeenCalledWith({
      kind: 'artifact', id: 'revenue-widget', back_link: '#/artifacts/revenue-widget',
    })
    const src = embedSrc()
    expect(src).toContain('/#/chat/sess-42?')
    expect(src).toContain('embed=1')
    const qs = new URLSearchParams(src.split('?')[1] ?? '')
    expect(qs.get('seed')).toBe('Iterate on artifact `revenue-widget`.')
    expect(onSession).toHaveBeenCalledWith('sess-42')
  })

  it('stages exactly ONE session even after the host rewrites the session prop', async () => {
    investigate.mockResolvedValue({ session_key: 'sess-42', context: { opening_prompt: 'go' } })
    const { view } = mount()
    await waitFor(() => expect(embedSrc()).not.toBe(''))

    view.rerender(<ArtifactIteratePanel slug="revenue-widget" name="Revenue widget"
      session="sess-42" onSession={vi.fn()} onClose={vi.fn()} />)
    await act(async () => {})

    expect(investigate).toHaveBeenCalledTimes(1)
    expect(embedSrc()).toContain('/#/chat/sess-42?')
  })

  it('resumes a deep-linked session without staging a new one', async () => {
    mount('sess-earlier')
    await act(async () => {})
    expect(investigate).not.toHaveBeenCalled()
    expect(embedSrc()).toContain('/#/chat/sess-earlier?')
  })

  it('names itself after the artifact so two panels never announce identically', async () => {
    investigate.mockResolvedValue({ session_key: 's', context: {} })
    mount()
    await act(async () => {})
    expect(screen.getByRole('complementary', { name: 'Iterate with agent: Revenue widget' })).toBeTruthy()
  })

  it('is closable', async () => {
    investigate.mockResolvedValue({ session_key: 's', context: {} })
    const { onClose } = mount()
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: /Close the iterate panel/i }))
    expect(onClose).toHaveBeenCalled()
  })
})

describe('the panel fills its column (a SOURCE rail — jsdom has no layout)', () => {
  const source = readFileSync(join(process.cwd(), "src/features/artifacts/ArtifactIteratePanel.tsx"), 'utf8')
  const m = source.match(/<aside[\s\S]{0,400}?className="([^"]*)"/)

  it('found the aside and its class string (not vacuous)', () => {
    expect(m, 'no <aside … className> in the panel source — the rail below would assert nothing').not.toBeNull()
    expect(m![1].length).toBeGreaterThan(10)
  })

  it('the aside grows into its track and is allowed to shrink', () => {
    const classes = m![1]
    expect(classes, `aside classes: ${classes}`).toContain('flex-1')
    expect(classes, `aside classes: ${classes}`).toContain('min-h-0')
  })
})

describe('a failure to stage is its own state, not an endless spinner', () => {
  it('names the failure, offers a retry, and mounts no embed', async () => {
    investigate.mockRejectedValueOnce(new Error('HTTP 500 investigate'))
    mount()

    await waitFor(() => expect(screen.queryByText(/Couldn't open an iteration session/)).not.toBeNull())
    expect(screen.getByText(/HTTP 500 investigate/)).toBeTruthy()
    expect(embedSrc()).toBe('')
    expect(screen.queryByText(/Loading the iteration session/)).toBeNull()
  })

  it('recovers on retry', async () => {
    investigate.mockRejectedValueOnce(new Error('HTTP 500 investigate'))
    mount()
    await waitFor(() => expect(screen.queryByText(/Couldn't open an iteration session/)).not.toBeNull())

    investigate.mockResolvedValue({ session_key: 'sess-later', context: { opening_prompt: 'go' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Try again/i })) })

    expect(screen.queryByText(/Couldn't open an iteration session/)).toBeNull()
    expect(embedSrc()).toContain('/#/chat/sess-later?')
  })
})
