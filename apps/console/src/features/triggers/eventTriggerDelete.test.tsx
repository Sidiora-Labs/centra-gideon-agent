import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { eventToTrigger } from './triggerMeta'
import { EventTriggerSummary } from './TriggersListPage'
import { api, type Trigger as WireTrigger } from '../../shared/data/api'


const confirmDelete = vi.fn<(entity: string, name?: string) => Promise<boolean>>()
vi.mock('../../shared/ui/dialog', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  confirmDelete: (...args: [string, string?]) => confirmDelete(...args),
}))

const wire = {
  kind: 'event', id: 'event:memo', raw_id: 'memo', name: 'On a memory write', enabled: true,
  pattern: 'MemoryKeyPattern', key_glob: 'project.acme.*', fire_count: 3,
  action: { provider: 'create-task', config: {} },
} as unknown as WireTrigger

beforeEach(() => {
  confirmDelete.mockReset()
})

describe('EventTriggerSummary delete', () => {
  it('confirmed → deletes by raw id and reports back through onDeleted', async () => {
    confirmDelete.mockResolvedValue(true)
    const del = vi.spyOn(api, 'deleteEventTrigger').mockResolvedValue(undefined)
    const onDeleted = vi.fn()
    render(<EventTriggerSummary t={eventToTrigger(wire)} onDeleted={onDeleted} />)
    fireEvent.click(screen.getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(onDeleted).toHaveBeenCalled())
    expect(del).toHaveBeenCalledWith('memo')
    expect(confirmDelete).toHaveBeenCalledWith('data-event trigger', 'On a memory write')
    del.mockRestore()
  })

  it('declined → nothing is written and the panel stays', async () => {
    confirmDelete.mockResolvedValue(false)
    const del = vi.spyOn(api, 'deleteEventTrigger').mockResolvedValue(undefined)
    const onDeleted = vi.fn()
    render(<EventTriggerSummary t={eventToTrigger(wire)} onDeleted={onDeleted} />)
    fireEvent.click(screen.getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    expect(del).not.toHaveBeenCalled()
    expect(onDeleted).not.toHaveBeenCalled()
    del.mockRestore()
  })

  it('a foreign event trigger offers no Delete at all', () => {
    const foreign = eventToTrigger({ ...wire, read_only: true, author: 'alice' } as unknown as WireTrigger)
    render(<EventTriggerSummary t={foreign} onDeleted={() => {}} />)
    expect(screen.queryByRole('button', { name: /delete/i })).toBeNull()
  })
})
