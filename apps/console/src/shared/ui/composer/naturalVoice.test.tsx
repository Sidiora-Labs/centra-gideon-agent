import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { NaturalVoicePill } from './controls'


describe('the natural-voice pill shows what actually takes effect', () => {
  const noop = vi.fn()

  it('announces its dimension and the effective state', () => {
    render(<NaturalVoicePill choice="on" effective source="conversation" agentDefault={false} onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Plain' })).toBeTruthy()
  })

  it('names the AGENT when the agent is what turned it on', () => {
    render(<NaturalVoicePill choice="" effective source="agent" agentDefault onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Plain (agent)' })).toBeTruthy()
  })

  it('reads Default when nothing asked for it', () => {
    render(<NaturalVoicePill choice="" effective={false} source="platform" agentDefault={false} onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Default' })).toBeTruthy()
  })

  it('an OFF override reads Default, not "Plain" — the agent does not win', () => {
    render(<NaturalVoicePill choice="off" effective={false} source="conversation" agentDefault onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Default' })).toBeTruthy()
  })

  it('before a conversation exists it shows the CHOICE, never a guessed effect', () => {
    render(<NaturalVoicePill choice="" effective={false} source="" agentDefault onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Agent default' })).toBeTruthy()
  })

  it('and a pre-session pick reads back as that pick', () => {
    render(<NaturalVoicePill choice="on" effective={false} source="" agentDefault={false} onSelect={noop} />)
    expect(screen.getByRole('button', { name: 'Natural voice: Plain' })).toBeTruthy()
  })

  it('is rendered at all — the state is visible without opening anything', () => {
    render(<NaturalVoicePill choice="" effective source="agent" agentDefault onSelect={noop} />)
    expect(screen.getByRole('button').textContent).toContain('Plain')
  })
})

describe('the pill does not re-derive the resolution order', () => {
  const SRC = readFileSync(join(process.cwd(), "src/shared/ui/composer/controls.tsx"), 'utf8')

  it('no local resolution of choice against the agent default', () => {
    const at = SRC.indexOf('export function NaturalVoicePill')
    expect(at, 'the component moved — this rail measures nothing').toBeGreaterThan(-1)
    const body = SRC.slice(at, SRC.indexOf('\n}\n', at))
    const from = body.indexOf('const label =')
    expect(from, 'the label expression moved — this rail measures nothing').toBeGreaterThan(-1)
    const labelExpr = body.slice(from, body.indexOf('return (', from))
    expect(labelExpr.length, 'empty slice — vacuous').toBeGreaterThan(20)
    expect(labelExpr, 'the label must come from the backend, not from agentDefault')
      .not.toMatch(/agentDefault/)
    expect(labelExpr).toMatch(/effective/)
    expect(labelExpr).toMatch(/source/)
    expect(body).toMatch(/hint=\{agentDefault \?/)
  })

  it('the composer renders it whenever the host supplies it', () => {
    const COMPOSER = readFileSync(join(process.cwd(), "src/shared/ui/Composer.tsx"), 'utf8')
    expect(COMPOSER).toMatch(/\{naturalVoice && <NaturalVoicePill \{\.\.\.naturalVoice\} \/>\}/)
  })
})
