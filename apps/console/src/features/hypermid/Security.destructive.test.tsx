import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { api, type HypermidSecurityStatusWire } from '../../shared/data/api'
import { resetDataStore, writeQuery } from '../../shared/data/data'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'
import { closeDialog, getDialogs } from '../../shared/ui/dialog/dialogStore'
import { Security } from './Security'

const status: HypermidSecurityStatusWire = {
  scope: { owner_id: 'owner', project_id: 'project' }, availability: 'ready', decisions: [],
  grants: [{ grantId: 'opaque-grant-identity', principalId: 'principal', operation: 'model.fetch', scheme: 'https', hostname: 'models.example.test', ports: [443], addressClasses: ['public'], proxyPolicy: 'required', redirectLimit: 2, byteLimit: 4096, expiresAtMs: Date.now() + 60000 }],
}
afterEach(() => {
  cleanup()
  for (const dialog of getDialogs()) closeDialog(dialog.id, false)
  vi.restoreAllMocks(); resetDataStore()
})
describe('native network grant destructive confirmation', () => {
  it('names the selected destination, cancels without revocation and confirms the same actual grant', async () => {
    writeQuery('hypermid:security:policy', status)
    writeQuery('hypermid:effects', { scope: status.scope, availability: 'ready', effects: [] })
    writeQuery('hypermid:security:credentials', { scope: status.scope, availability: 'ready', credentials: [] })
    vi.spyOn(api, 'hypermidSecurityPolicy').mockResolvedValue(status)
    const revoke = vi.spyOn(api, 'revokeHypermidNetworkGrant').mockResolvedValue({} as Awaited<ReturnType<typeof api.revokeHypermidNetworkGrant>>)
    render(<><Security /><DialogHost /></>)
    fireEvent.click(screen.getByRole('button', { name: 'Revoke', exact: true }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('Revoke network access to https://models.example.test?')).toBeTruthy()
    expect(dialog.textContent).not.toContain('opaque-grant-identity')
    expect(within(dialog).getByText('Future requests covered by this grant will be denied. Active effects remain subject to their authoritative outcome state.')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel', exact: true }))
    await waitFor(() => expect(getDialogs()).toHaveLength(0))
    expect(revoke).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Revoke', exact: true }))
    const confirmed = await screen.findByRole('alertdialog')
    fireEvent.click(within(confirmed).getByRole('button', { name: 'Revoke grant', exact: true }))
    await waitFor(() => expect(revoke).toHaveBeenCalledExactlyOnceWith('opaque-grant-identity'))
  })
})
