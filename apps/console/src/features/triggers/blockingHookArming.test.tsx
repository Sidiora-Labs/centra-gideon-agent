import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { hookToTrigger } from './triggerMeta'
import { LifecycleDetail } from './LifecycleDetail'
import type { HookItem } from '../../shared/data/api'


vi.mock('../schedule/ScheduleDetail', () => ({ RunHistory: () => null }))

const hook = (over: Partial<HookItem> = {}): HookItem => ({
  id: 'h1', name: 'aap3-pretool', event: 'PreToolUse', matcher: '',
  provider: 'bash', provider_config: { command: 'exit 2' },
  timeout: 30, enabled: true, last_run: 0, last_status: '', run_count: 0, used_by: [],
  ...over,
})

const detail = (h: HookItem) => render(
  <LifecycleDetail
    hook={h}
    providers={[{ name: 'bash', display_name: 'Bash', supports_blocking: true, settingsSchema: {} }]}
    onSaved={() => {}}
    onDeleted={() => {}}
    editing={false}
    onEditingChange={() => {}}
  />,
)

describe('hookToTrigger carries the server enforcement verdict', () => {
  it('passes blocking + enforcement through onto the list view-model', () => {
    const t = hookToTrigger(hook({ blocking: true, enforcement: 'not_enforcing' }))
    expect(t.blocking).toBe(true)
    expect(t.enforcement).toBe('not_enforcing')
  })

  it('does not re-derive enforcement from used_by', () => {
    const bound = hookToTrigger(hook({ used_by: ['coder'], enabled: false, blocking: true, enforcement: 'not_enforcing' }))
    expect(bound.usedBy).toEqual(['coder'])
    expect(bound.enforcement).toBe('not_enforcing')
  })

  it('makes no claim when the server sends none', () => {
    const t = hookToTrigger(hook())
    expect(t.enforcement).toBeUndefined()
    expect(t.blocking).toBeUndefined()
  })
})

describe('LifecycleDetail on a blocking hook', () => {
  it('says NOT ENFORCING for an unbound one, and says what to do about it', () => {
    detail(hook({ blocking: true, enforcement: 'not_enforcing' }))
    expect(screen.getByText(/not enforcing/i)).toBeTruthy()
    expect(screen.getByText(/blocking hook that cannot block/i)).toBeTruthy()
    expect(screen.getByText(/No agents reference this trigger yet/)).toBeTruthy()
  })

  it('says ENFORCING for a bound one — a DIFFERENT rendering, not silence', () => {
    detail(hook({ blocking: true, enforcement: 'enforcing', used_by: ['coder'] }))
    expect(screen.getByText(/^Enforcing$/)).toBeTruthy()
    expect(screen.queryByText(/not enforcing/i)).toBeNull()
    expect(screen.queryByText(/cannot block/i)).toBeNull()
  })

  it('annotates a non-zero run count on an unarmed hook — the measured misread', () => {
    detail(hook({ blocking: true, enforcement: 'not_enforcing', run_count: 3 }))
    expect(screen.getByText(/Ran 3×/)).toBeTruthy()
    expect(screen.getByText(/runs were advisory/i)).toBeTruthy()
  })

  it('leaves a non-blocking hook unlabelled either way', () => {
    detail(hook({ event: 'Stop', blocking: false, enforcement: 'advisory' }))
    expect(screen.queryByText(/enforcing/i)).toBeNull()
    expect(screen.getByText(/it's dormant/)).toBeTruthy()
  })
})
