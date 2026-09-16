import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { SourcesPanel } from './SourcesPanel'


const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.resolve({}))
const gideonConfig = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: {
    gideonConfig: () => gideonConfig(),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
  },
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../shared/data/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    useEffect(() => { fn().then(setData) }, [])
    return { data }
  },
}))

describe('the watched-scratchpad path control', () => {
  beforeEach(() => {
    patchConfig.mockClear()
    gideonConfig.mockResolvedValue({
      sources: { enabled: true },
      planning: { scratchpad_path: '/Users/me/notes/today.md' },
    })
  })

  it('renders the configured path — a field nobody reads is indistinguishable from an unset one', async () => {
    render(<SourcesPanel />)
    const input = await waitFor(() => screen.getByDisplayValue('/Users/me/notes/today.md'))
    expect(input).toBeTruthy()
  })

  it('names itself for assistive tech via the Field it sits in', async () => {
    render(<SourcesPanel />)
    await waitFor(() => screen.getByDisplayValue('/Users/me/notes/today.md'))
    expect(screen.getByRole('textbox', { name: /scratchpad path/i })).toBeTruthy()
  })

  it('PATCHes the allowlisted dotted path on Save, and not per keystroke', async () => {
    render(<SourcesPanel />)
    const input = await waitFor(() => screen.getByDisplayValue('/Users/me/notes/today.md'))
    fireEvent.change(input, { target: { value: '/Users/me/notes/inbox.md' } })
    expect(patchConfig).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
    expect(patchConfig).toHaveBeenCalledWith('planning.scratchpad_path', '/Users/me/notes/inbox.md')
  })

  it('an emptied field saves "" — that is how the user turns intake off', async () => {
    render(<SourcesPanel />)
    const input = await waitFor(() => screen.getByDisplayValue('/Users/me/notes/today.md'))
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
    expect(patchConfig).toHaveBeenCalledWith('planning.scratchpad_path', '')
  })
})
