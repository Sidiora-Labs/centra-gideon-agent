import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TRIGGER_PRESETS } from './triggerPresets'


const { createSchedule, PROVIDERS } = vi.hoisted(() => ({
  createSchedule: vi.fn((_body: Record<string, unknown>) => Promise.resolve({ id: 'sched-1' })),
  PROVIDERS: [
    {
      name: 'invoke-agent', display_name: 'Invoke Agent', supports_blocking: false,
      settingsSchema: {
        type: 'object', required: ['task_template'],
        properties: {
          task_template: { type: 'string', 'x-meta': { label: 'Task' } },
          agent: { type: 'string', 'x-meta': { label: 'Agent' } },
          approval_mode: { type: 'string', enum: ['', 'auto'], default: '', 'x-meta': { label: 'Approval' } },
        },
      },
    },
    {
      name: 'notify', display_name: 'Notification', supports_blocking: false,
      settingsSchema: {
        type: 'object', required: ['title_template'],
        properties: {
          title_template: { type: 'string', 'x-meta': { label: 'Title' } },
          body_template: { type: 'string', 'x-meta': { label: 'Body' } },
          kind: { type: 'string', enum: ['info', 'success'], default: 'info', 'x-meta': { label: 'Kind' } },
        },
      },
    },
  ],
}))

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    actionProviders: () => Promise.resolve(PROVIDERS),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: ['$NOW'], event: [] }),
    savedAgents: () => Promise.resolve([]),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve({}),
    prompts: () => Promise.resolve([]),
    createSchedule,
    createHook: vi.fn(),
    createEvent: vi.fn(),
  },
}))

const { TriggerCreatePage } = await import('./TriggerCreatePage')

const mount = (query: Record<string, string>) => render(
  <TriggerCreatePage onBack={() => {}} onCreated={() => {}} query={query} setQuery={() => {}} />,
)

const nameInput = () => screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement

beforeEach(() => {
  createSchedule.mockClear()
  sessionStorage.clear()
})

describe('#/triggers/new?preset=… — the seeded flow', () => {
  it('opens holding the preset\'s name, cadence and action', async () => {
    mount({ kind: 'schedule', preset: 'morning-briefing' })

    expect(nameInput().value).toBe('Morning briefing')
    const cron = await waitFor(() => screen.getByRole('textbox', { name: /Cron expression/i }) as HTMLInputElement)
    expect(cron.value).toBe('0 8 * * *')
    await waitFor(() => expect(screen.getByText('Invoke Agent')).toBeInTheDocument())
    const task = await waitFor(() => screen.getByRole('textbox', { name: /^Task/ }) as HTMLTextAreaElement)
    expect(task.value).toContain('morning briefing')
    expect(screen.getByText(/Filled in from the/)).toBeInTheDocument()
  })

  it('saves a working schedule trigger on the first press of Create', async () => {
    mount({ kind: 'schedule', preset: 'morning-briefing' })
    const create = await waitFor(() => {
      const b = screen.getByRole('button', { name: /^Create trigger/ })
      expect(b).not.toHaveAttribute('aria-disabled')
      return b
    })
    await userEvent.click(create)

    await waitFor(() => expect(createSchedule).toHaveBeenCalledTimes(1))
    const body = createSchedule.mock.calls[0][0]
    expect(body.name).toBe('Morning briefing')
    expect(body.cron).toBe('0 8 * * *')
    expect(body.every).toBeUndefined()
    expect(body.action).toEqual({
      provider: 'invoke-agent',
      config: expect.objectContaining({
        task_template: expect.stringContaining('morning briefing'),
      }),
    })
    const cfg = (body.action as { config: Record<string, unknown> }).config
    expect(cfg.agent).toBeUndefined()
    expect(cfg.approval_mode).toBeUndefined()
  })

  it('keeps a notify preset\'s declared value over the schema default', async () => {
    mount({ kind: 'schedule', preset: 'standup-reminder' })
    const create = await waitFor(() => {
      const b = screen.getByRole('button', { name: /^Create trigger/ })
      expect(b).not.toHaveAttribute('aria-disabled')
      return b
    })
    await userEvent.click(create)
    await waitFor(() => expect(createSchedule).toHaveBeenCalledTimes(1))
    const body = createSchedule.mock.calls[0][0]
    expect(body.cron).toBe('45 9 * * 1-5')
    expect(body.action).toEqual({
      provider: 'notify',
      config: {
        title_template: 'Standup in 15 minutes',
        body_template: 'Jot down what you finished yesterday and what you are picking up today.',
        kind: 'info',
      },
    })
  })

  it('covers every preset in the catalog — each one arrives savable', async () => {
    for (const p of TRIGGER_PRESETS) {
      const view = mount({ kind: 'schedule', preset: p.id })
      await waitFor(() => expect(screen.getByRole('button', { name: /^Create trigger/ })).not.toHaveAttribute('aria-disabled'))
      view.unmount()
    }
  })
})

describe('#/triggers/new — the expert blank path, unchanged', () => {
  it('opens empty and refuses to create', async () => {
    mount({})
    expect(nameInput().value).toBe('')
    expect(screen.queryByText(/Filled in from the/)).not.toBeInTheDocument()
    expect(screen.getByText('Pick an action…')).toBeInTheDocument()
    const create = await waitFor(() => {
      const b = screen.getByRole('button', { name: /^Create trigger/ })
      expect(b).toHaveAttribute('aria-disabled', 'true')
      return b
    })
    expect(create.getAttribute('title')).toContain('Name the trigger first')
    await userEvent.click(create)
    expect(createSchedule).not.toHaveBeenCalled()
  })

  it('still defaults to the interval cadence, not a preset\'s cron', async () => {
    mount({})
    expect(screen.queryByRole('textbox', { name: /Cron expression/i })).not.toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: /interval count/i })).toBeInTheDocument()
  })

  it('treats an unknown preset id as the blank path rather than guessing', async () => {
    mount({ kind: 'schedule', preset: 'not-a-preset' })
    expect(nameInput().value).toBe('')
    expect(screen.queryByText(/Filled in from the/)).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /^Create trigger/ })).toHaveAttribute('aria-disabled', 'true'))
  })
})
