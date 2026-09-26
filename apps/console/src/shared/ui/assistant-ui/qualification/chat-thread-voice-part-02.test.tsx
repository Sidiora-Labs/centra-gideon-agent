import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ThreadSharedSnapshots, ThreadConversationSearch, formatPrivateCopyTime } from '../../../../features/chat/auiThreadSurfaces'
import type { ChatTurn } from '../../../../features/chat/chatTypes'
import type { ChatSessionShare, ChatSessionShareDetail } from '../../../data/api'

afterEach(cleanup)

const first: ChatSessionShare = {
  slug: 'private-copy-1', name: 'Review (shared chat)', created_at: '2026-09-26T12:00:00Z', shared_by: 'Sir',
  url: '#/artifacts/private-copy-1', audience: 'private', readonly: true, redacted: true,
}
const second: ChatSessionShare = {
  slug: 'private-copy-2', name: 'Earlier review (shared chat)', created_at: '2026-09-25T12:00:00Z', shared_by: null,
  url: '#/artifacts/private-copy-2', audience: 'private', readonly: true, redacted: true,
}
const detail: ChatSessionShareDetail = {
  ...first,
  turns: [
    { id: '0', role: 'user', text: 'Check [REDACTED] credentials' },
    { id: '1', role: 'assistant', text: 'The secret is redacted in this copy.' },
  ],
}

describe('owner-only read-only conversation copies', () => {
  it('renders stored redacted snapshot turns and true attribution, with explicit actions', () => {
    const create = vi.fn()
    const select = vi.fn()
    const open = vi.fn()
    const copy = vi.fn()
    const revoke = vi.fn()
    const { container } = render(<ThreadSharedSnapshots shares={[first, second]} selected={first.slug} detail={detail} busy={false} error={null}
      onCreate={create} onSelect={select} onOpen={open} onCopy={copy} onRevoke={revoke}/>)
    expect(container.querySelectorAll('[data-slot="shared-conversation"]')).toHaveLength(1)
    expect(screen.getByText('Check [REDACTED] credentials')).toBeTruthy()
    expect(screen.getByText(/shared by Sir/)).toBeTruthy()
    expect(screen.queryByText(/2026-09-26T12:00:00Z/)).toBeNull()
    expect(screen.getByText(/available only to the owner/)).toBeTruthy()
    expect(screen.queryByText('Check my live unredacted credentials')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Create private copy' }))
    expect(create).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: second.name }))
    expect(select).toHaveBeenCalledWith(second.slug)
    fireEvent.click(screen.getAllByRole('button', { name: 'Open read-only copy' })[0])
    expect(open).toHaveBeenCalledWith(first)
    fireEvent.click(screen.getAllByRole('button', { name: 'Copy private link' })[0])
    expect(copy).toHaveBeenCalledWith(first)
    fireEvent.click(screen.getAllByRole('button', { name: 'Revoke copy' })[0])
    expect(revoke).toHaveBeenCalledWith(first)
  })

  it('formats only a valid stored timestamp for private attribution', () => {
    expect(formatPrivateCopyTime(first.created_at)).toMatch(/2026/)
    expect(formatPrivateCopyTime('not-a-date')).toBeUndefined()
  })

  it('does not imply attribution or history that the stored artifact does not provide', () => {
    const unknownOwner: ChatSessionShareDetail = { ...second, turns: [] }
    render(<ThreadSharedSnapshots shares={[second]} selected={second.slug} detail={unknownOwner} busy error="Could not revoke this copy"
      onCreate={() => {}} onSelect={() => {}} onOpen={() => {}} onCopy={() => {}} onRevoke={() => {}}/>)
    expect(screen.queryByText(/shared by/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Continue in your own chat/ })).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain('Could not revoke this copy')
    expect(screen.getByRole('button', { name: 'Create private copy' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'Revoke copy' }).hasAttribute('disabled')).toBe(true)
  })

  it('does not show a stale snapshot while selection switches to another stored copy', () => {
    const choose = vi.fn()
    const { rerender, container } = render(<ThreadSharedSnapshots shares={[first, second]} selected={first.slug} detail={detail} busy={false} error={null}
      onCreate={() => {}} onSelect={choose} onOpen={() => {}} onCopy={() => {}} onRevoke={() => {}}/>)
    expect(container.querySelector('[data-slot="shared-conversation"]')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: second.name }))
    expect(choose).toHaveBeenCalledWith(second.slug)
    rerender(<ThreadSharedSnapshots shares={[first, second]} selected={second.slug} detail={detail} busy={false} error={null}
      onCreate={() => {}} onSelect={choose} onOpen={() => {}} onCopy={() => {}} onRevoke={() => {}}/>)
    expect(container.querySelector('[data-slot="shared-conversation"]')).toBeNull()
    expect(screen.queryByText('Check [REDACTED] credentials')).toBeNull()
  })
})

describe('search through connected transcript segments', () => {
  it('finds real tool and error content and jumps to the matching rendered turn', () => {
    const toolNode = document.createElement('div')
    const errorNode = document.createElement('div')
    toolNode.scrollIntoView = vi.fn()
    errorNode.scrollIntoView = vi.fn()
    const turns: ChatTurn[] = [
      { role: 'assistant', segments: [{ kind: 'tool', id: 'read-1', tool: 'read_file', detail: 'report.csv', done: true }] },
      { role: 'assistant', segments: [{ kind: 'error', text: 'Could not read report.csv' }] },
    ]
    render(<ThreadConversationSearch turns={turns} nodeOf={(index) => index === 0 ? toolNode : errorNode} onClose={() => {}}/>)
    fireEvent.change(screen.getByRole('textbox', { name: 'Find in conversation' }), { target: { value: 'report.csv' } })
    expect(screen.getByText('1/2')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next match' }))
    expect(errorNode.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
    expect(toolNode.scrollIntoView).not.toHaveBeenCalled()
  })
})
