import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { NoModelSetupState, isNoModelSetupError, MODELS_ROUTE } from './NoModelSetupState'


const NO_MODEL_ENVELOPE = [
  "WHAT: no model provider resolves for use case 'chat'",
  'WHY: no provider in config.json declares the capability this use case needs',
  "FIX: add a model provider in Settings → Providers, then bind 'chat' to it",
].join('\n')

const STALE_PIN_ENVELOPE = [
  "WHAT: the model pinned for use case 'chat' ('Bedrock:global.anthropic.claude-opus-4-8') cannot be built",
  "WHY: the active ref names provider 'Bedrock', which is absent from config.json (its app isn't installed or configured)",
  "FIX: install 'Bedrock' in the App Store, or rebind 'chat' to an available model in Settings → Models",
].join('\n')

describe('isNoModelSetupError', () => {
  it('matches the no-model-configured envelope', () => {
    expect(isNoModelSetupError(NO_MODEL_ENVELOPE)).toBe(true)
  })

  it('does NOT match the stale-pin variant (a model was chosen, config.json is mentioned)', () => {
    expect(isNoModelSetupError(STALE_PIN_ENVELOPE)).toBe(false)
  })

  it('does NOT match unrelated turn errors or empty input', () => {
    expect(isNoModelSetupError('The model returned an error.')).toBe(false)
    expect(isNoModelSetupError('WHAT: a tool argument failed validation\nWHY: …\nFIX: …')).toBe(false)
    expect(isNoModelSetupError('')).toBe(false)
    expect(isNoModelSetupError(null)).toBe(false)
    expect(isNoModelSetupError(undefined)).toBe(false)
  })
})

describe('NoModelSetupState', () => {
  it('leads with a plain sentence and the way forward, not the raw envelope', () => {
    render(<NoModelSetupState detail={NO_MODEL_ENVELOPE} onSetup={() => {}} />)
    expect(screen.getByText('No model connected yet')).toBeTruthy()
    expect(screen.getByText(/connect a model to start chatting/i)).toBeTruthy()
    expect(screen.queryByText(/^error$/i)).toBeNull()
  })

  it('keeps the full WHAT/WHY/FIX detail behind a collapsed disclosure', () => {
    const { container } = render(<NoModelSetupState detail={NO_MODEL_ENVELOPE} onSetup={() => {}} />)
    const details = container.querySelector('details')
    expect(details).not.toBeNull()
    expect(details!.hasAttribute('open')).toBe(false)
    const pre = details!.querySelector('pre')
    expect(pre?.textContent).toContain('no model provider resolves for use case')
    expect(pre?.textContent).toContain('config.json')
  })

  it('offers a CTA that hands navigation to the router (no raw hash write)', () => {
    const onSetup = vi.fn()
    render(<NoModelSetupState detail={NO_MODEL_ENVELOPE} onSetup={onSetup} />)
    fireEvent.click(screen.getByRole('button', { name: /set up a model/i }))
    expect(onSetup).toHaveBeenCalledTimes(1)
  })

  it('agrees with DegradedChip on the destination', () => {
    expect(MODELS_ROUTE).toBe('#/settings/models')
  })
})
