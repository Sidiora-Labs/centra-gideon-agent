import { useRef, useState } from 'react'
import { EditorView } from '@codemirror/view'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SelectionQuote, UserEditor } from '../../../../features/ChatPage'
import { Composer } from '../../Composer'
import { appendSelectionQuote, measuredContextDisplay, readActualContextUsage } from '../../../../features/chat/auiThreadSurfaces'
import { GideonChatRuntimeProvider } from '../../../../features/chat/auiRuntime'
import { ThreadFollowupSuggestions } from '../../../vendor/assistant-ui/elements/follow-up-suggestions.aui'

afterEach(cleanup)

describe('selection quote into the sole Gideon editor', () => {
  it('previews the actual selection in donor QuoteBlock and inserts it once into CodeMirror', () => {
    function Host() {
      const viewport = useRef<HTMLDivElement>(null)
      const [draft, setDraft] = useState('Existing draft')
      return <>
        <div ref={viewport}>
          <span data-testid="source-excerpt">Check the deployment log</span>
          <SelectionQuote scrollRef={viewport} attributionFor={() => 'Gideon'}
            onQuote={(text, source) => setDraft((previous) => appendSelectionQuote(previous, text, source))}/>
        </div>
        <Composer value={draft} onChange={setDraft} onSend={() => {}} onStop={() => {}} onAttach={() => {}}
          controls={{ attach: false, model: false, agent: false, slash: false }}
          data={{ agents: [], providers: [], discovered: {}, models: [] }}
          selection={{ agent: 'Gideon', model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' }}/>
      </>
    }
    render(<Host/>)
    const source = screen.getByTestId('source-excerpt')
    const selectSource = () => {
      const range = document.createRange()
      range.selectNodeContents(source)
      range.getBoundingClientRect = () => new DOMRect(10, 10, 160, 16)
      window.getSelection()?.removeAllRanges()
      window.getSelection()?.addRange(range)
      fireEvent.mouseUp(source)
    }
    selectSource()
    expect(document.querySelector('[data-slot="quote-block"]')).toBeTruthy()
    expect(document.querySelector('[data-slot="quote-block-text"]')?.textContent).toBe('Check the deployment log')
    fireEvent.click(screen.getByRole('button', { name: 'Quote' }))
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    const text = EditorView.findFromDOM(editor)!.state.doc.toString()
    expect(text).toBe('Existing draft\n\n> **Gideon said:**\n> Check the deployment log\n\n')
    expect(text.match(/Check the deployment log/g)).toHaveLength(1)
    expect(document.querySelector('[data-slot="quote-block"]')).toBeNull()

    const clipboard = vi.fn().mockResolvedValue(undefined)
    const previousClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: clipboard } })
    try {
      selectSource()
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
      expect(clipboard).toHaveBeenCalledWith('Check the deployment log')
      expect(document.querySelector('[data-slot="quote-block"]')).toBeNull()
      expect(EditorView.findFromDOM(editor)!.state.doc.toString()).toBe(text)
    } finally {
      if (previousClipboard) Object.defineProperty(navigator, 'clipboard', previousClipboard)
      else Reflect.deleteProperty(navigator, 'clipboard')
      window.getSelection()?.removeAllRanges()
    }
  })
})

describe('connected user edit and resend', () => {
  it('uses donor EditMessage with the actual number of later assistant replies', () => {
    const resend = vi.fn()
    const cancel = vi.fn()
    const { container } = render(<UserEditor initial="Original request" discardedReplies={2} onSubmit={resend} onCancel={cancel}/>)
    expect(container.querySelector('[data-slot="edit-message"]')).toBeTruthy()
    expect(screen.getByText(/sending discards 2 replies/)).toBeTruthy()
    const editor = screen.getByRole('textbox', { name: 'Edit your message' })
    fireEvent.change(editor, { target: { value: 'Updated request' } })
    fireEvent.click(screen.getByRole('button', { name: 'Resend' }))
    expect(resend).toHaveBeenCalledExactlyOnceWith('Updated request')
    expect(cancel).not.toHaveBeenCalled()
  })

  it('preserves empty-message blocking and Escape/keyboard resend', () => {
    const resend = vi.fn()
    const cancel = vi.fn()
    render(<UserEditor initial="Original request" onSubmit={resend} onCancel={cancel}/>)
    const editor = screen.getByRole('textbox', { name: 'Edit your message' })
    fireEvent.change(editor, { target: { value: '  ' } })
    expect(screen.getByRole('button', { name: 'Resend' }).hasAttribute('disabled')).toBe(true)
    fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true })
    expect(resend).not.toHaveBeenCalled()
    fireEvent.change(editor, { target: { value: 'Valid correction' } })
    fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true })
    expect(resend).toHaveBeenCalledExactlyOnceWith('Valid correction')
    fireEvent.keyDown(editor, { key: 'Escape' })
    expect(cancel).toHaveBeenCalledOnce()
  })
})

describe('measured session context usage', () => {
  it('maps exclusive provider buckets into a truthful context total', () => {
    const value = readActualContextUsage({
      input_tokens: 120,
      cache_creation_tokens: 30,
      cache_read_tokens: 50,
      context_window_tokens: 1000,
    })
    expect(value).toEqual({ input_tokens: 120, cache_creation_tokens: 30, cache_read_tokens: 50, context_window_tokens: 1000 })
    expect(measuredContextDisplay(value)).toEqual({
      modelContextWindow: 1000,
      usage: { totalTokens: 200, inputTokens: 150, cachedInputTokens: 50 },
    })
  })

  it('keeps unavailable cache or window measurements unknown', () => {
    expect(measuredContextDisplay(readActualContextUsage({
      input_tokens: 120,
      cache_creation_tokens: null,
      cache_read_tokens: null,
      context_window_tokens: 1000,
    }))).toBeNull()
    expect(measuredContextDisplay(readActualContextUsage({
      input_tokens: 120,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      context_window_tokens: null,
    }))).toBeNull()
    expect(measuredContextDisplay(readActualContextUsage({
      input_tokens: 0,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      context_window_tokens: 0,
    }))).toBeNull()
  })

  it('uses a directly measured provider input total without inventing missing cache buckets', () => {
    const direct = readActualContextUsage({
      input_tokens: null,
      cache_creation_tokens: null,
      cache_read_tokens: null,
      context_window_tokens: 8192,
      total_input_tokens: 512,
    })
    expect(measuredContextDisplay(direct)).toEqual({ modelContextWindow: 8192, usage: { totalTokens: 512 } })
    expect(readActualContextUsage({ ...direct, total_input_tokens: -1 })).toBeUndefined()
    expect(readActualContextUsage({ ...direct, total_input_tokens: '512' })).toBeUndefined()
  })

  it('rejects partial, malformed, or negative socket measurements instead of inferring zero', () => {
    expect(readActualContextUsage({ input_tokens: 2, context_window_tokens: 1000 })).toBeUndefined()
    expect(readActualContextUsage({ input_tokens: '2', cache_creation_tokens: 0, cache_read_tokens: 0, context_window_tokens: 1000 })).toBeUndefined()
    expect(readActualContextUsage({ input_tokens: Number.NaN, cache_creation_tokens: 0, cache_read_tokens: 0, context_window_tokens: 1000 })).toBeUndefined()
    expect(measuredContextDisplay(readActualContextUsage({
      input_tokens: -2,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      context_window_tokens: 1000,
    }))).toBeNull()
  })

  it('distinguishes a measured zero-token prompt from missing usage', () => {
    const measuredZero = readActualContextUsage({
      input_tokens: 0,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      context_window_tokens: 200000,
    })
    expect(measuredContextDisplay(measuredZero)).toEqual({
      modelContextWindow: 200000,
      usage: { totalTokens: 0, inputTokens: 0, cachedInputTokens: 0 },
    })
    expect(measuredContextDisplay(undefined)).toBeNull()
  })
})

describe('follow-up suggestions from the live thread adapter', () => {
  it('fills the Gideon draft without sending until the user presses Send', () => {
    const pick = vi.fn()
    const send = vi.fn()
    render(<GideonChatRuntimeProvider sessionId="s1" turns={[
      { role: 'user', segments: [{ kind: 'text', text: 'Investigate this' }] },
      { role: 'assistant', segments: [{ kind: 'text', text: 'I found two leads' }] },
    ]} streaming={false} suggestions={['Check the first lead', 'Open the second lead']}
      onSend={send} onStop={() => {}} onEdit={() => {}} onReload={() => {}}>
      <ThreadFollowupSuggestions sendOnSelect={false} onPick={pick}/>
    </GideonChatRuntimeProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Check the first lead' }))
    expect(pick).toHaveBeenCalledWith('Check the first lead')
    expect(send).not.toHaveBeenCalled()
  })

  it('does not offer stale suggestions during a running answer', () => {
    render(<GideonChatRuntimeProvider sessionId="s1" turns={[
      { role: 'user', segments: [{ kind: 'text', text: 'Investigate this' }] },
      { role: 'assistant', segments: [{ kind: 'text', text: 'Working' }] },
    ]} streaming suggestions={['Wait for details']}
      onSend={() => {}} onStop={() => {}} onEdit={() => {}} onReload={() => {}}>
      <ThreadFollowupSuggestions sendOnSelect={false} onPick={() => {}}/>
    </GideonChatRuntimeProvider>)
    expect(screen.queryByRole('button', { name: 'Wait for details' })).toBeNull()
  })
})
