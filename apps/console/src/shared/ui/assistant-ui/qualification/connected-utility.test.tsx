import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { LauncherBubble } from '../../../vendor/assistant-ui/elements/launcher-bubble'
import { SharedConversation } from '../../../vendor/assistant-ui/elements/shared-conversation'
import { CanvasSplit, CanvasSplitDocument, CanvasSplitHeader, CanvasSplitBody, CanvasSplitLine } from '../../../vendor/assistant-ui/elements/canvas-split'

describe('source-derived workspace launcher', () => {
  it('shows the real greeting and dispatches supplied workspace actions without a reply-time claim', () => {
    const called: string[] = []
    render(<LauncherBubble open unread={0} greeting="Workspace assistant" prompts={['Open task']}
      onToggle={() => called.push('toggle')} onPick={prompt => called.push(prompt)} onStart={() => called.push('start')} />)
    expect(screen.getByText('Workspace assistant')).toBeInTheDocument()
    expect(screen.queryByText(/replies in/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open task' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start a conversation' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close the assistant' }))
    expect(called).toEqual(['Open task', 'start', 'toggle'])
  })

  it('shows reply timing only when the caller supplies a recorded value', () => {
    const { rerender } = render(<LauncherBubble open unread={0} greeting="Help" prompts={[]} />)
    expect(screen.queryByText(/replies/)).toBeNull()
    rerender(<LauncherBubble open unread={0} greeting="Help" prompts={[]} replyTime="Replies during staffed hours" />)
    expect(screen.getByText('Replies during staffed hours')).toBeInTheDocument()
  })
})

describe('source-derived canvas for a real file viewer', () => {
  it('omits unknown version and saved labels while retaining supplied file actions', () => {
    const called: string[] = []
    const { container } = render(<CanvasSplit><CanvasSplitDocument>
      <CanvasSplitHeader title="Real file.txt" actions={<button onClick={() => called.push('expand')}>Expand</button>}
        onCopy={() => called.push('copy')} onClose={() => called.push('close')} />
      <CanvasSplitBody><CanvasSplitLine>Current file contents</CanvasSplitLine></CanvasSplitBody>
    </CanvasSplitDocument></CanvasSplit>)
    expect(container.querySelector('[data-slot="canvas-split"]')).toBeInTheDocument()
    expect(screen.getByText('Current file contents')).toBeInTheDocument()
    expect(screen.queryByText(/^v\d/)).toBeNull()
    expect(screen.queryByText('saved')).toBeNull()
    expect(screen.queryByText('editing')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }))
    fireEvent.click(screen.getByRole('button', { name: 'Copy Real file.txt' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close the canvas' }))
    expect(called).toEqual(['expand', 'copy', 'close'])
  })

  it('shows known version and save state and omits unavailable copy or close controls', () => {
    const { rerender } = render(<CanvasSplitHeader title="Record" version={3} saved />)
    expect(screen.getByText('v3')).toBeInTheDocument()
    expect(screen.getByText('saved')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Copy|Close/ })).toBeNull()
    rerender(<CanvasSplitHeader title="Record" version={4} saved={false} />)
    expect(screen.getByText('v4')).toBeInTheDocument()
    expect(screen.getByText('editing')).toBeInTheDocument()
  })
})


describe('source-derived owner-only conversation preview', () => {
  const turns = [{ id: 'one', role: 'user' as const, text: 'Visible owner request' }]

  it('hides unknown share metadata and unavailable continue action', () => {
    render(<SharedConversation title="My transcript" turns={turns} />)
    expect(screen.getByText('Visible owner request')).toBeInTheDocument()
    expect(screen.queryByText(/shared by/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Continue in your own chat' })).toBeNull()
    expect(screen.getByText('read only')).toBeInTheDocument()
  })

  it('shows recorded owner share metadata and dispatches a supplied continue action', () => {
    let continued = 0
    render(<SharedConversation title="My transcript" turns={turns} sharedBy="Owner" sharedAt="Today" onContinue={() => { continued += 1 }} />)
    expect(screen.getByText('shared by Owner · Today')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Continue in your own chat' }))
    expect(continued).toBe(1)
  })
})
