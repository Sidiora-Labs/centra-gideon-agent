import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const { preview, save, onSaved } = vi.hoisted(() => ({ preview: vi.fn(), save: vi.fn(), onSaved: vi.fn() }))
vi.mock('../../shared/data/api', async original => ({ ...(await original<Record<string, unknown>>()), api: {
  skills: async () => [{ key: 'one', name: 'One' }, { key: 'two', name: 'Two' }],
  tools: async () => [{ name: 'read_file' }, { name: 'bash' }], hooks: async () => [],
  previewAgentGrants: preview, updateAgent: save,
} }))
vi.mock('../../shared/data/agents', () => ({ useActiveChatModelOptions: () => ({ options: [] }) }))
import { NativeAgentDetail } from './AgentDetail'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'

function editor() {
  render(<><NativeAgentDetail agent={{ name: 'reader', provider: 'native', tools: ['read_file'], skills: ['one'] }} isDefault={false} onSaved={onSaved} onDeleted={() => {}} onSetDefault={() => {}} editing onEditingChange={() => {}} /><DialogHost /></>)
}
afterEach(() => { cleanup(); vi.clearAllMocks() })
describe('native agent grant review', () => {
  it('shows the exact server offer, cancellation leaves the store untouched, then explicit consent carries its receipt', async () => {
    preview.mockResolvedValue({ confirmation_required: true, grant_receipt: 'exact-offer', changes: [{ field: 'tools', before: ['read_file'], after: ['read_file', 'bash'], every: false }] })
    save.mockResolvedValue({ ok: true })
    editor()
    await userEvent.click(await screen.findByRole('button', { name: 'bash' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Tools: read_file → read_file, bash')).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(save).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    const confirmed = await screen.findByRole('dialog')
    await userEvent.click(within(confirmed).getByRole('button', { name: 'Confirm and save' }))
    await waitFor(() => expect(save).toHaveBeenCalledOnce())
    expect(save.mock.calls[0][1]).toMatchObject({ tools: ['read_file', 'bash'], grant_receipt: 'exact-offer' })
    expect(onSaved).toHaveBeenCalledOnce()
  })
})
