import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import type { ApprovalSegment } from './chatTypes'
import { decodeBlastRadius } from './approvalMeta'
import { ApprovalNotifications } from '../../app/shell/approvalNotifications'

afterEach(cleanup)
const segment = (more: Partial<ApprovalSegment>): ApprovalSegment => ({ kind: 'approval', id: 'request', tool: 'bash', toolKind: 'write', ...more })

describe('authoritative command effect approvals', () => {
  it('renders established backend effects instead of guessing from the tool name', () => {
    const { container } = render(<ApprovalCard seg={segment({ risk: 'safe', blastRadius: { writes: false, shell: false, network: false, readOnly: true } })} onAct={() => {}} />)
    expect(container.textContent).toContain('Reads only')
    expect(container.textContent).not.toContain('Runs a command')
    expect(container.textContent).not.toContain('Writes files')
  })
  it('labels unchecked calls honestly and requires an explicit standing-choice unlock', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={segment({ risk: 'unchecked', blastRadius: { writes: false, shell: true, network: false, readOnly: false } })} onAct={onAct} />)
    expect(screen.getByText('Not checked')).toBeTruthy()
    expect(screen.queryByRole('tab', { name: 'This chat' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Allow bash/ }))
    expect(onAct).toHaveBeenCalledWith('request', 'approved')
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('tab', { name: 'This chat' }))
    fireEvent.click(screen.getByRole('button', { name: /^Allow bash/ }))
    expect(onAct).toHaveBeenLastCalledWith('request', 'trust')
  })
  it('offers only this call for protected removal', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={segment({ risk: 'destructive', protectedDelete: 'This would delete the working folder.', input: 'rm -rf .' })} onAct={onAct} />)
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.queryByRole('tab', { name: 'This chat' })).toBeNull()
    expect(screen.queryByRole('tab', { name: 'This agent' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Allow bash/ }))
    expect(onAct).toHaveBeenCalledWith('request', 'approved')
  })
  it('carries identical facets into actual out-of-chat notifications', () => {
    const notices = new ApprovalNotifications()
    const message = notices.receive({ type: 'approval', data: { session: 'other', id: 'a', tool: 'bash', risk: 'safe', blast_radius: { writes: false, network: false, shell: false, readOnly: true } } }, 'main')
    expect(message).toContain('reads only')
    expect(message).not.toContain('runs a command')
    expect(notices.receive({ type: 'approval', data: { session: 'other', id: 'a' } }, 'main')).toBeUndefined()
    expect(decodeBlastRadius({ readOnly: 'true' })).toBeUndefined()
  })
})
