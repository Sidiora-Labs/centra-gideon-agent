import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import type { ActionProvider, Trigger as WireTrigger } from '../../shared/data/api'
import type { RouteProps } from '../../app/shell/useQueryState'
import { resetDataStore, writeQuery } from '../../shared/data/data'
import { TRIGGER_PRESETS } from './triggerPresets'
import { TriggersSection } from './TriggersSection'

const populatedTrigger: WireTrigger = {
  kind: 'store',
  id: 'store:manual:heartbeat-tasks',
  raw_id: 'manual:heartbeat-tasks',
  name: 'Heartbeat tasks',
  enabled: true,
  action: { provider: 'notify', config: {} },
  store_kind: 'manual',
  health: 'ok',
  state: 'active',
  run_count: 0,
  broken: [],
  warnings: [],
}

const actionProviders: ActionProvider[] = [
  {
    name: 'invoke-agent',
    display_name: 'Invoke Agent',
    supports_blocking: false,
    settingsSchema: {
      type: 'object',
      required: ['task_template'],
      properties: { task_template: { type: 'string', 'x-meta': { label: 'Task' } } },
    },
  },
  {
    name: 'notify',
    display_name: 'Notification',
    supports_blocking: false,
    settingsSchema: {
      type: 'object',
      required: ['title_template', 'body_template'],
      properties: {
        title_template: { type: 'string', 'x-meta': { label: 'Title' } },
        body_template: { type: 'string', 'x-meta': { label: 'Body' } },
      },
    },
  },
]

function seedQueryStore() {
  writeQuery('triggers:schedules', [])
  writeQuery('triggers:hooks', [])
  writeQuery('triggers:store', [populatedTrigger])
  writeQuery('triggers:action-providers', actionProviders)
}

function AppRoute() {
  const [route, setRoute] = useState<{ sub: string; query: Record<string, string> }>({ sub: '', query: {} })
  const navigate: RouteProps['navigate'] = (to) => {
    const [path, rawQuery = ''] = to.split('?')
    setRoute({
      sub: path.replace(/^triggers\/?/, ''),
      query: Object.fromEntries(new URLSearchParams(rawQuery)),
    })
  }
  const setQuery: RouteProps['setQuery'] = (patch) => setRoute((current) => {
    const query = { ...current.query }
    for (const [key, value] of Object.entries(patch)) {
      if (value == null || value === '') delete query[key]
      else query[key] = value
    }
    return { ...current, query }
  })

  return <TriggersSection sub={route.sub} navigate={navigate} navEpoch={0} query={route.query} setQuery={setQuery} />
}

beforeEach(() => {
  resetDataStore()
  seedQueryStore()
})

describe('preset creation from a populated trigger home', () => {
  it('reaches every preset from "New trigger", and a pick fills the form', async () => {
    render(<AppRoute />)
    await screen.findByText('Heartbeat tasks')

    for (const preset of TRIGGER_PRESETS) {
      await userEvent.click(screen.getByRole('button', { name: /New trigger/ }))
      const gallery = await screen.findByRole('group', { name: 'Start from a preset' })
      for (const available of TRIGGER_PRESETS) {
        expect(within(gallery).getByRole('button', { name: `${available.title} — ${available.summary}` })).toBeInTheDocument()
      }

      await userEvent.click(within(gallery).getByRole('button', { name: `${preset.title} — ${preset.summary}` }))
      await waitFor(() => {
        expect((screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement).value).toBe(preset.prefill.name)
      })
      expect(screen.getByDisplayValue(preset.prefill.name)).toBeInTheDocument()
      expect(screen.getByDisplayValue(preset.prefill.cadence.kind === 'weekly'
        ? '0 9 * * 1'
        : preset.prefill.cadence.kind === 'weekdays'
          ? '45 9 * * 1-5'
          : preset.prefill.cadence.kind === 'daily' && preset.prefill.cadence.hour === 23
            ? '0 23 * * *'
            : '0 8 * * *')).toBeInTheDocument()
      const configValue = Object.values(preset.prefill.config)[0]
      expect(screen.getByDisplayValue(String(configValue))).toBeInTheDocument()
      expect(screen.getByText(/Filled in from the/)).toBeInTheDocument()
      expect(screen.queryByRole('group', { name: 'Start from a preset' })).not.toBeInTheDocument()

      if (preset !== TRIGGER_PRESETS.at(-1)) {
        await userEvent.click(screen.getByRole('button', { name: 'Back' }))
        await screen.findByText('Heartbeat tasks')
      }
    }
  })
})
