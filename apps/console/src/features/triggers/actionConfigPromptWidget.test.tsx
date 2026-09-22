import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const prompts = vi.fn(async () => [{ name: 'weekly-review', description: 'Review the week' }])

vi.mock('../../shared/data/api', () => ({ api: { prompts } }))

import { ActionConfig } from './ActionConfig'
import type { ActionProvider } from '../../shared/data/api'

afterEach(() => {
  cleanup()
  prompts.mockClear()
})

const PROMPT_ACTION: ActionProvider = {
  name: 'run-prompt',
  display_name: 'Run prompt',
  supports_blocking: false,
  settingsSchema: {
    type: 'object',
    properties: {
      prompt_id: { type: 'string', 'x-meta': { label: 'Saved prompt', widget: 'prompt' } },
    },
  },
}

function renderConfig(provider: ActionProvider, onConfig = vi.fn()) {
  render(
    <ActionConfig providers={[provider]} provider={provider.name} config={{}}
      onProvider={() => {}} onConfig={onConfig} vars={[]} />,
  )
  return onConfig
}

describe('ActionConfig prompt widgets', () => {
  it('fetches saved prompts and routes an x-meta.widget field through SchemaField', async () => {
    const onConfig = renderConfig(PROMPT_ACTION)

    await waitFor(() => expect(prompts).toHaveBeenCalledWith('user'))
    fireEvent.click(screen.getByRole('button', { name: 'Pick a saved prompt…' }))
    fireEvent.click(await screen.findByRole('option', { name: 'weekly-review' }))

    expect(onConfig).toHaveBeenCalledWith({ prompt_id: 'weekly-review' })
  })

  it('does not fetch prompts for fields without the prompt widget', async () => {
    renderConfig({
      ...PROMPT_ACTION,
      settingsSchema: { type: 'object', properties: { message: { type: 'string' } } },
    })

    await Promise.resolve()
    expect(prompts).not.toHaveBeenCalled()
  })
})
