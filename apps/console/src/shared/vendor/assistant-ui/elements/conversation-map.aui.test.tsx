import { act, cleanup, fireEvent, render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AssistantRuntimeProvider, useExternalStoreRuntime, type ThreadMessageLike } from '@assistant-ui/react'
import { useEffect, useState } from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { ThreadTranscript } from './thread.aui'
import { ConversationMapAui } from './conversation-map.aui'

type RecordMessage = { id: string; role: 'user' | 'assistant'; text: string }
const user = (id: string, text: string): RecordMessage => ({ id, role: 'user', text })
const assistant = (id: string, text: string): RecordMessage => ({ id, role: 'assistant', text })
const VIEWPORT_HEIGHT = 300

function Runtime({ records }: { records: RecordMessage[] }) {
  const [messages, setMessages] = useState(records)
  useEffect(() => setMessages(records), [records])
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (record: RecordMessage): ThreadMessageLike => ({
      id: record.id,
      role: record.role,
      content: record.text ? [{ type: 'text', text: record.text }] : [],
    }),
    onNew: async content => {
      const text = content.content.map(part => part.type === 'text' ? part.text : '').join('')
      setMessages(current => [...current, user(`u${current.length + 1}`, text)])
    },
  })
  return <AssistantRuntimeProvider runtime={runtime}>
    <ThreadTranscript afterMessages={<ConversationMapAui />} />
  </AssistantRuntimeProvider>
}

const rect = (top: number, height: number) =>
  ({ top, height, bottom: top + height }) as DOMRect

async function frame() {
  await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)) })
}

async function mount(records: RecordMessage[], tops: Record<string, number>, scrollHeight = VIEWPORT_HEIGHT * 4) {
  const view = render(<Runtime records={records} />)
  const viewport = view.container.querySelector<HTMLElement>('[data-slot="aui_thread-viewport"]')!
  expect(viewport).not.toBeNull()
  viewport.getBoundingClientRect = () => rect(0, VIEWPORT_HEIGHT)
  Object.defineProperty(viewport, 'clientHeight', { configurable: true, value: VIEWPORT_HEIGHT })
  Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: scrollHeight })
  viewport.scrollTo = vi.fn()
  const position = () => {
    for (const element of viewport.querySelectorAll<HTMLElement>('[data-message-id]')) {
      const id = element.dataset.messageId!
      expect(id in tops).toBe(true)
      element.getBoundingClientRect = () => rect(tops[id]!, 50)
    }
  }
  position()
  await frame()
  vi.mocked(viewport.scrollTo).mockClear()
  const update = async (next: RecordMessage[]) => {
    view.rerender(<Runtime records={next} />)
    position()
    await frame()
  }
  return { viewport, position, update, ...view }
}

const ticks = () => Array.from(document.querySelectorAll<HTMLElement>('[data-slot="conversation-map-tick"]'))
const labels = () => ticks().map(tick => tick.getAttribute('aria-label'))
const active = () => ticks().map(tick => tick.hasAttribute('data-active'))
const inView = () => ticks().map(tick => tick.hasAttribute('data-in-view'))

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

afterEach(() => cleanup())

describe('ConversationMapAui with real assistant runtime and viewport', () => {
  it('groups actual user and assistant message roots into turns', async () => {
    const records = [
      user('u1', 'Can you check the extension build?'),
      assistant('a1', 'It is the unpacked one.'),
      assistant('a2', 'Reload it.'),
      user('u2', 'Ready to reload?'),
      assistant('a3', 'Not yet.'),
    ]
    const { viewport } = await mount(records, { u1: 0, a1: 60, a2: 120, u2: 180, a3: 240 })
    expect([...viewport.querySelectorAll('[data-message-id]')].map(node => node.getAttribute('data-message-id')))
      .toEqual(['u1', 'a1', 'a2', 'u2', 'a3'])
    expect(labels()).toEqual(['Can you check the extension build?', 'Ready to reload?'])
  })

  it('starts a turn for a leading answer and each consecutive user message', async () => {
    await mount(
      [assistant('a0', 'How can I help?'), user('u1', 'First'), user('u2', 'Second'), assistant('a1', 'Answering both.')],
      { a0: 0, u1: 60, u2: 120, a1: 180 },
    )
    expect(labels()).toEqual(['How can I help?', 'First', 'Second'])
  })

  it('tracks the turn owning an assistant message at the reading line and both visible turns', async () => {
    await mount(
      [user('u1', 'First'), assistant('a1', 'One.'), user('u2', 'Second'), assistant('a2', 'Two.'), user('u3', 'Third')],
      { u1: -220, a1: -30, u2: -10, a2: 120, u3: 400 },
    )
    expect(active()).toEqual([false, true, false])
    expect(inView()).toEqual([true, true, false])
  })

  it('moves the active turn on an actual viewport scroll event', async () => {
    const tops = { u1: 0, u2: 200 }
    const { viewport } = await mount([user('u1', 'First'), user('u2', 'Second')], tops)
    expect(active()).toEqual([true, false])
    tops.u1 = -220
    tops.u2 = -20
    await act(async () => {
      viewport.dispatchEvent(new Event('scroll'))
      await new Promise(resolve => requestAnimationFrame(resolve))
    })
    expect(active()).toEqual([false, true])
  })

  it('reaches the final turn in the final screenful', async () => {
    const { viewport } = await mount(
      [user('u1', 'First'), user('u2', 'Second'), user('u3', 'Third'), user('u4', 'Fourth')],
      { u1: -200, u2: -50, u3: 100, u4: 250 },
      VIEWPORT_HEIGHT * 2,
    )
    viewport.scrollTop = VIEWPORT_HEIGHT
    await act(async () => {
      viewport.dispatchEvent(new Event('scroll'))
      await new Promise(resolve => requestAnimationFrame(resolve))
    })
    expect(active()).toEqual([false, false, false, true])
  })

  it('selects the real message root by scrolling only the thread viewport', async () => {
    const { viewport } = await mount(
      [user('u1', 'First'), assistant('a1', 'One.'), user('u2', 'Second')],
      { u1: 0, a1: 60, u2: 120 },
    )
    viewport.scrollTop = 100
    vi.mocked(viewport.scrollTo).mockClear()
    fireEvent.click(ticks()[1]!)
    expect(viewport.scrollTo).toHaveBeenCalledWith({ top: 220, behavior: 'smooth' })
    expect(ticks()[1]).toHaveAttribute('aria-label', 'Second')
  })

  it('preserves keyboard navigation and updates turns as the real store changes', async () => {
    const records = [user('u1', 'First'), assistant('a1', 'One.')]
    const tops = { u1: 0, a1: 60, u2: 120 }
    const { viewport, update } = await mount(records, tops)
    expect(labels()).toEqual(['First'])
    await update([...records, user('u2', 'Second')])
    expect(labels()).toEqual(['First', 'Second'])
    act(() => ticks()[0]!.focus())
    fireEvent.keyDown(ticks()[0]!, { key: 'End' })
    expect(document.activeElement).toBe(ticks()[1])
    await userEvent.keyboard('{Enter}')
    expect(viewport.scrollTo).toHaveBeenCalledWith({ top: 120, behavior: 'smooth' })
  })
})
