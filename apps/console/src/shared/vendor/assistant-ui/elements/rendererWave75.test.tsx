import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { AssistantRuntimeProvider, MessagePrimitive, ThreadPrimitive, useExternalStoreRuntime, type ThreadMessageLike } from '@assistant-ui/react'
import { Activity, useState, type ReactNode } from 'react'
import type { SyntaxHighlighterProps } from '@assistant-ui/react-markdown'
import { MarkdownText } from './markdown-text'
import { SyntaxHighlighter as PrismHighlighter } from './syntax-highlighter'
import { SyntaxHighlighter as ShikiHighlighter } from './shiki-highlighter'
import { SyntaxHighlighter as AuiShikiHighlighter } from './shiki-highlighter.aui'
import { MermaidDiagram as BaseMermaid, MermaidZoom } from './mermaid-diagram'
import { MermaidDiagram as AuiMermaid } from './mermaid-diagram.aui'
import { useCopyToClipboard } from '../hooks/use-copy-to-clipboard'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

type StoredMessage = { id: string; role: 'user' | 'assistant'; text: string }
function Runtime({ children, text = 'Hello from Gideon' }: { children: ReactNode; text?: string }) {
  const [messages, setMessages] = useState<StoredMessage[]>([{ id: 'assistant-one', role: 'assistant', text }])
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (message: StoredMessage): ThreadMessageLike => ({
      id: message.id,
      role: message.role,
      content: [{ type: 'text', text: message.text }],
    }),
    onNew: async content => {
      const answer = content.content.map(part => part.type === 'text' ? part.text : '').join('')
      setMessages(current => [...current, { id: `user-${current.length}`, role: 'user', text: answer }])
    },
  })
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}
function Parts({ components }: { components?: Record<string, unknown> }) {
  return <ThreadPrimitive.Root><ThreadPrimitive.Viewport autoScroll={false} scrollToBottomOnInitialize={false}
    scrollToBottomOnRunStart={false} scrollToBottomOnThreadSwitch={false}>
    <ThreadPrimitive.Messages components={{
      AssistantMessage: () => <MessagePrimitive.Root><MessagePrimitive.Parts
        components={components as never} /></MessagePrimitive.Root>,
    }} />
  </ThreadPrimitive.Viewport></ThreadPrimitive.Root>
}
function ClipboardProbe() {
  const { isCopied, copyToClipboard } = useCopyToClipboard({ copiedDuration: 500 })
  return <><button onClick={() => copyToClipboard('actual snippet')}>Copy snippet</button>
    <span data-testid="copy-state">{isCopied ? 'copied' : 'ready'}</span></>
}
function clipboard(writeText: (value: string) => Promise<void>) {
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
}

const validMermaid = 'graph TD\n A[Start] --> B[Done]'
const invalidMermaid = 'this is not a mermaid graph'

describe('MarkdownText connected to actual assistant message parts', () => {
  it('renders plain text from the runtime message', () => {
    render(<Runtime><Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByText('Hello from Gideon')).toBeTruthy()
    expect(document.querySelector('.aui-md')).toBeTruthy()
  })
  it('renders heading semantics from a Markdown part', () => {
    render(<Runtime text="# Release status"><Parts components={{ Text: MarkdownText }} /></Runtime>)
    const heading = screen.getByRole('heading', { name: 'Release status', level: 1 })
    expect(heading.className).toContain('aui-md-h1')
    expect(heading.className).toContain('font-semibold')
  })
  it('renders lower heading levels through donor overrides', () => {
    render(<Runtime text={'## Stage two\n\n### Stage three\n\n#### Stage four'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByRole('heading', { name: 'Stage two', level: 2 }).className).toContain('aui-md-h2')
    expect(screen.getByRole('heading', { name: 'Stage three', level: 3 }).className).toContain('aui-md-h3')
    expect(screen.getByRole('heading', { name: 'Stage four', level: 4 }).className).toContain('aui-md-h4')
  })
  it('renders final heading levels without flattening them into paragraphs', () => {
    render(<Runtime text={'##### Stage five\n\n###### Stage six'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByRole('heading', { name: 'Stage five', level: 5 }).className).toContain('aui-md-h5')
    expect(screen.getByRole('heading', { name: 'Stage six', level: 6 }).className).toContain('aui-md-h6')
    expect(screen.queryByRole('heading', { level: 4 })).toBeNull()
  })
  it('renders emphasis, strong text and inline code in the message scope', () => {
    render(<Runtime text={'A **strong** word and `inline code`.'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByText('strong').tagName).toBe('STRONG')
    const code = screen.getByText('inline code')
    expect(code.tagName).toBe('CODE')
    expect(code.className).toContain('aui-md-inline-code')
  })
  it('renders lists using actual GFM content instead of example records', () => {
    render(<Runtime text={'- First\n- Second\n\n1. Third\n2. Fourth'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByText('First').closest('ul')?.className).toContain('aui-md-ul')
    expect(screen.getByText('Fourth').closest('ol')?.className).toContain('aui-md-ol')
    expect(screen.getAllByRole('listitem')).toHaveLength(4)
  })
  it('renders blockquote and horizontal rule with source styles', () => {
    const { container } = render(<Runtime text={'> Cited work\n\n---'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByText('Cited work').closest('blockquote')?.className).toContain('aui-md-blockquote')
    expect(container.querySelector('hr.aui-md-hr')).toBeTruthy()
  })
  it('gives a real GFM table a bounded horizontal container', () => {
    render(<Runtime text={'| Key | Value |\n| --- | --- |\n| a | long value |'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    const table = screen.getByRole('table')
    expect(table.className).toContain('aui-md-table')
    expect(table.parentElement?.className).toContain('overflow-x-auto')
    expect(screen.getByText('long value').closest('td')?.className).toContain('aui-md-td')
  })
  it('renders a normal link as a link with Gideon-compatible source styling', () => {
    render(<Runtime text={'[Guide](https://docs.example/guide)'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    const link = screen.getByRole('link', { name: 'Guide' })
    expect(link.getAttribute('href')).toBe('https://docs.example/guide')
    expect(link.className).toContain('aui-md-a')
  })
  it('renders a fenced code block with its language and copy affordance', () => {
    render(<Runtime text={'```ts\nconst value = 42;\n```'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByText('ts')).toBeTruthy()
    expect(screen.getByText(/const value = 42/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy()
  })
  it('keeps code block text as plain text rather than executing markup', () => {
    const { container } = render(<Runtime text={'```html\n<script>bad()</script>\n```'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByText(/<script>bad\(\)<\/script>/)).toBeTruthy()
    expect(container.querySelector('script')).toBeNull()
  })
})

const prismComponents: SyntaxHighlighterProps['components'] = {
  Pre: props => <pre {...props} />,
  Code: props => <code {...props} />,
}

describe('Prism syntax highlighter source behavior', () => {
  it('keeps a real TypeScript snippet available in light and dark surfaces', async () => {
    const { container } = render(<PrismHighlighter components={prismComponents} code="const answer: number = 42" language="ts" />)
    expect(container.querySelector('.dark\\:hidden')).toBeTruthy()
    expect(container.querySelector('.dark\\:block')).toBeTruthy()
    await waitFor(() => expect(container.textContent).toContain('const answer'))
  })
  it('supports Python without inventing a language translation', async () => {
    const { container } = render(<PrismHighlighter components={prismComponents} code="print('ok')" language="python" />)
    await waitFor(() => expect(container.textContent).toContain("print('ok')"))
  })
  it('renders separate light and dark trees for the same caller code', async () => {
    const { container } = render(<PrismHighlighter components={prismComponents} code="let value = 1" language="js" />)
    await waitFor(() => expect(container.querySelectorAll('pre').length).toBeGreaterThanOrEqual(2))
    const visible = [...container.querySelectorAll('pre')].map(node => node.textContent)
    expect(visible.every(value => value?.includes('let value'))).toBe(true)
  })
  it('changes highlighted code when caller content changes', async () => {
    const view = render(<PrismHighlighter components={prismComponents} code="let before = 1" language="js" />)
    await waitFor(() => expect(view.container.textContent).toContain('before'))
    view.rerender(<PrismHighlighter components={prismComponents} code="let after = 2" language="js" />)
    await waitFor(() => expect(view.container.textContent).toContain('after'))
    expect(view.container.textContent).not.toContain('before')
  })
})

describe('Shiki base and assistant-ui adapter', () => {
  it('streams trimmed plain code without waiting for tokenization', () => {
    const { container } = render(<ShikiHighlighter code={'  const a = 1  '} language="typescript" streaming />)
    expect(container.querySelector('.aui-shiki-streaming')).toBeTruthy()
    expect(screen.getByText('const a = 1')).toBeTruthy()
    expect(container.querySelectorAll('pre')).toHaveLength(1)
  })
  it('keeps explicit class and style on the code container', () => {
    const { container } = render(<ShikiHighlighter code="x" language="text" streaming
      className="consumer-highlight" style={{ maxWidth: 320 }} />)
    const code = container.querySelector('.aui-shiki-base') as HTMLElement
    expect(code.className).toContain('consumer-highlight')
    expect(code.style.maxWidth).toBe('320px')
  })
  it('uses actual Shiki output or real plain code while highlighting loads', async () => {
    const { container } = render(<ShikiHighlighter code="const number = 1" language="typescript" />)
    await waitFor(() => expect(container.textContent).toContain('const number = 1'))
    expect(container.querySelector('.aui-shiki-base')).toBeTruthy()
  })
  it('updates code when the caller supplies a new snippet', async () => {
    const view = render(<ShikiHighlighter code="first" language="text" streaming />)
    expect(screen.getByText('first')).toBeTruthy()
    view.rerender(<ShikiHighlighter code="second" language="text" streaming />)
    expect(screen.getByText('second')).toBeTruthy()
    expect(screen.queryByText('first')).toBeNull()
  })
  it('keeps adapter source identity and content under an assistant runtime', async () => {
    const { container } = render(<Runtime><AuiShikiHighlighter code="const answer = 1" language="typescript" /></Runtime>)
    await waitFor(() => expect(container.textContent).toContain('const answer = 1'))
    expect(AuiShikiHighlighter.displayName).toBe('SyntaxHighlighter')
  })
})

describe('Mermaid diagram source and zoom behavior', () => {
  it('shows a truthful rendering skeleton during streaming', () => {
    const { container } = render(<BaseMermaid code={validMermaid} streaming />)
    const skeleton = container.querySelector('[data-slot="mermaid-skeleton"]')
    expect(skeleton?.getAttribute('aria-label')).toBe('Rendering diagram')
    expect(container.querySelector('[data-slot="mermaid-diagram"]')).toBeNull()
  })
  it('keeps caller class on the streaming skeleton', () => {
    const { container } = render(<BaseMermaid code={validMermaid} streaming className="consumer-diagram" />)
    expect(container.querySelector('[data-slot="mermaid-skeleton"]')?.className).toContain('consumer-diagram')
  })
  it('renders SVG for a valid graph from caller code', async () => {
    const { container } = render(<BaseMermaid code={validMermaid} />)
    await waitFor(() => expect(container.querySelector('[data-slot="mermaid-diagram"] svg')).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Expand diagram' })).toBeTruthy()
  })
  it('shows the caller code with a failure message for invalid Mermaid input', () => {
    const { container } = render(<BaseMermaid code={invalidMermaid} />)
    expect(container.querySelector('[data-slot="mermaid-fallback"]')).toBeTruthy()
    expect(screen.getByText(invalidMermaid)).toBeTruthy()
    expect(screen.getByText('diagram could not be rendered')).toBeTruthy()
  })
  it('opens the actual zoom dialog and closes it through the button', () => {
    render(<BaseMermaid code={validMermaid} />)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    expect(screen.getByRole('dialog', { name: 'Diagram' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('opens zoom and closes it with Escape', () => {
    render(<BaseMermaid code={validMermaid} />)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('prevents body scrolling only while zoom is open', () => {
    document.body.style.overflow = 'auto'
    render(<BaseMermaid code={validMermaid} />)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(document.body.style.overflow).toBe('auto')
  })
  it('zooms in, out and resets from the real dialog controls', () => {
    render(<BaseMermaid code={validMermaid} />)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const content = document.querySelector('[data-slot="mermaid-zoom-content"]') as HTMLElement
    expect(content.style.transform).toContain('scale(1)')
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    expect(content.style.transform).toContain('scale(1.25)')
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }))
    expect(content.style.transform).toContain('scale(1)')
    fireEvent.click(screen.getByRole('button', { name: 'Reset zoom' }))
    expect(content.style.transform).toContain('scale(1)')
  })
  it('keeps the zoom button accessible after the dialog closes', () => {
    render(<BaseMermaid code={validMermaid} />)
    const trigger = screen.getByRole('button', { name: 'Expand diagram' })
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(document.activeElement).toBe(trigger)
  })
  it('renders the AUI wrapper in an actual assistant runtime', async () => {
    const { container } = render(<Runtime><AuiMermaid code={validMermaid} language="mermaid" /></Runtime>)
    await waitFor(() => expect(container.querySelector('[data-slot="mermaid-diagram"] svg')).toBeTruthy())
    expect(AuiMermaid.Zoom).toBe(MermaidZoom)
  })
})

describe('useCopyToClipboard lifecycle with the browser clipboard API', () => {
  beforeEach(() => vi.useFakeTimers())
  it('writes the exact caller text and marks it copied only after success', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    clipboard(writeText)
    const { result } = renderHook(() => useCopyToClipboard())
    expect(result.current.isCopied).toBe(false)
    await act(async () => {
      result.current.copyToClipboard('actual snippet')
      await Promise.resolve()
    })
    expect(writeText).toHaveBeenCalledExactlyOnceWith('actual snippet')
    expect(result.current.isCopied).toBe(true)
  })
  it('keeps confirmation visible until its configured duration expires', async () => {
    clipboard(vi.fn().mockResolvedValue(undefined))
    const { result } = renderHook(() => useCopyToClipboard({ copiedDuration: 1800 }))
    await act(async () => {
      result.current.copyToClipboard('copy me')
      await Promise.resolve()
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(1799) })
    expect(result.current.isCopied).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(result.current.isCopied).toBe(false)
  })
  it('restarts the full confirmation duration after a second successful copy', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    clipboard(writeText)
    const { result } = renderHook(() => useCopyToClipboard({ copiedDuration: 1800 }))
    await act(async () => {
      result.current.copyToClipboard('first')
      await Promise.resolve()
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    await act(async () => {
      result.current.copyToClipboard('second')
      await Promise.resolve()
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(1799) })
    expect(result.current.isCopied).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(result.current.isCopied).toBe(false)
    expect(writeText.mock.calls).toEqual([['first'], ['second']])
  })
  it('does not say copied when the browser rejects the clipboard write', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('Permission denied'))
    clipboard(writeText)
    const { result } = renderHook(() => useCopyToClipboard())
    await act(async () => {
      result.current.copyToClipboard('private snippet')
      await Promise.resolve()
    })
    expect(result.current.isCopied).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
    expect(writeText).toHaveBeenCalledWith('private snippet')
  })
  it('does not call a missing clipboard API or claim success', async () => {
    vi.stubGlobal('navigator', { ...navigator, clipboard: undefined })
    const { result } = renderHook(() => useCopyToClipboard())
    await act(async () => { result.current.copyToClipboard('value') })
    expect(result.current.isCopied).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
  })
  it('does not call the clipboard API for an empty string', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    clipboard(writeText)
    const { result } = renderHook(() => useCopyToClipboard())
    await act(async () => { result.current.copyToClipboard('') })
    expect(writeText).not.toHaveBeenCalled()
    expect(result.current.isCopied).toBe(false)
  })
  it('cleans up a pending confirmation timer on unmount', async () => {
    clipboard(vi.fn().mockResolvedValue(undefined))
    const { result, unmount } = renderHook(() => useCopyToClipboard())
    await act(async () => {
      result.current.copyToClipboard('value')
      await Promise.resolve()
    })
    expect(vi.getTimerCount()).toBe(1)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
  it('ignores a clipboard success that resolves after the owner unmounts', async () => {
    let resolveCopy!: () => void
    clipboard(() => new Promise<void>(resolve => { resolveCopy = resolve }))
    const { result, unmount } = renderHook(() => useCopyToClipboard())
    result.current.copyToClipboard('value')
    unmount()
    resolveCopy()
    await act(async () => { await Promise.resolve() })
    expect(vi.getTimerCount()).toBe(0)
  })
  it('clears confirmation when a hidden React Activity cancels the hook scope', async () => {
    clipboard(vi.fn().mockResolvedValue(undefined))
    let state!: ReturnType<typeof useCopyToClipboard>
    function Probe() {
      state = useCopyToClipboard()
      return null
    }
    function Host({ mode }: { mode: 'visible' | 'hidden' }) {
      return <Activity mode={mode}><Probe /></Activity>
    }
    const view = render(<Host mode="visible" />)
    await act(async () => {
      state.copyToClipboard('first')
      await Promise.resolve()
    })
    expect(state.isCopied).toBe(true)
    view.rerender(<Host mode="hidden" />)
    view.rerender(<Host mode="visible" />)
    expect(state.isCopied).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
  })
  it('lets a mounted control expose the hook state to the user', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    clipboard(writeText)
    render(<ClipboardProbe />)
    expect(screen.getByTestId('copy-state').textContent).toBe('ready')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy snippet' }))
      await Promise.resolve()
    })
    expect(screen.getByTestId('copy-state').textContent).toBe('copied')
    expect(writeText).toHaveBeenCalledWith('actual snippet')
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(screen.getByTestId('copy-state').textContent).toBe('ready')
  })
})

describe('MermaidZoom direct interaction contract', () => {
  const svg = '<svg><defs><clipPath id="clip"><rect width="20" height="20"/></clipPath></defs><rect clip-path="url(#clip)"/><use href="#clip"/></svg>'
  const diagram = <div data-testid="original-diagram">Original rendered diagram</div>
  it('keeps the supplied preview visible before expansion', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    expect(screen.getByTestId('original-diagram')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Expand diagram' })).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('renders the zoomed copy in a modal portal', () => {
    const { container } = render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const dialog = screen.getByRole('dialog', { name: 'Diagram' })
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(dialog.parentElement).toBe(document.body)
    expect(container.querySelector('[data-slot="mermaid-zoom-overlay"]')).toBeNull()
  })
  it('rewrites SVG definition IDs and URL references in the zoom copy', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const zoom = document.querySelector('[data-slot="mermaid-zoom-content"]') as HTMLElement
    expect(zoom.innerHTML).toContain('id="clip-zoom"')
    expect(zoom.innerHTML).toContain('url(#clip-zoom)')
    expect(zoom.innerHTML).toContain('href="#clip-zoom"')
  })
  it('keeps original children outside the zoomed SVG', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const dialog = screen.getByRole('dialog', { name: 'Diagram' })
    expect(dialog.querySelector('[data-testid="original-diagram"]')).toBeNull()
    expect(screen.getByTestId('original-diagram')).toBeTruthy()
  })
  it('caps repeated zoom-in actions at four times the original scale', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const zoom = document.querySelector('[data-slot="mermaid-zoom-content"]') as HTMLElement
    for (let index = 0; index < 12; index++) fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    expect(zoom.style.transform).toContain('scale(4)')
  })
  it('caps repeated zoom-out actions at half the original scale', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const zoom = document.querySelector('[data-slot="mermaid-zoom-content"]') as HTMLElement
    for (let index = 0; index < 12; index++) fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }))
    expect(zoom.style.transform).toContain('scale(0.5)')
  })
  it('restores both pan and scale when Reset zoom is pressed', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const zoom = document.querySelector('[data-slot="mermaid-zoom-content"]') as HTMLElement
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    expect(zoom.style.transform).not.toBe('translate(0px, 0px) scale(1)')
    fireEvent.click(screen.getByRole('button', { name: 'Reset zoom' }))
    expect(zoom.style.transform).toBe('translate(0px, 0px) scale(1)')
  })
  it('uses wheel input to change scale while the zoom dialog is open', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const viewport = document.querySelector('.aui-mermaid-zoom-viewport') as HTMLElement
    fireEvent.wheel(viewport, { deltaY: -200, clientX: 10, clientY: 10 })
    const zoom = document.querySelector('[data-slot="mermaid-zoom-content"]') as HTMLElement
    expect(zoom.style.transform).not.toContain('scale(1)')
  })
  it('resets zoom state when closed and reopened', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const zoom = document.querySelector('[data-slot="mermaid-zoom-content"]') as HTMLElement
    expect(zoom.style.transform).toBe('translate(0px, 0px) scale(1)')
  })
  it('moves initial keyboard focus to Close inside the dialog', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close' }))
  })
  it('wraps forward Tab from the last control back to the first', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const close = screen.getByRole('button', { name: 'Close' })
    close.focus()
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    document.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Zoom in' }))
  })
  it('wraps reverse Tab from the first control to the last', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const first = screen.getByRole('button', { name: 'Zoom in' })
    first.focus()
    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
    document.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close' }))
  })
  it('does not intercept unrelated keypresses', () => {
    render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const event = new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true })
    document.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
    expect(screen.getByRole('dialog')).toBeTruthy()
  })
  it('restores existing body overflow after the zoom overlay unmounts', () => {
    document.body.style.overflow = 'scroll'
    const view = render(<MermaidZoom svg={svg}>{diagram}</MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    expect(document.body.style.overflow).toBe('hidden')
    view.unmount()
    expect(document.body.style.overflow).toBe('scroll')
  })
})

describe('Markdown donor rendering with caller overrides', () => {
  it('lets a consumer override heading presentation while preserving message data', () => {
    const Heading = ({ children }: { children?: ReactNode }) => <h1 data-testid="consumer-heading">{children}</h1>
    render(<Runtime text="# Current project"><Parts components={{ Text: () => <MarkdownText components={{ h1: Heading }} /> }} /></Runtime>)
    expect(screen.getByTestId('consumer-heading').textContent).toBe('Current project')
    expect(screen.queryByRole('heading', { name: 'Current project' })).toBeTruthy()
  })
  it('does not erase default paragraph rendering when heading is overridden', () => {
    const Heading = ({ children }: { children?: ReactNode }) => <h1 data-testid="custom-heading">{children}</h1>
    render(<Runtime text={'# Current project\n\nA paragraph'}>
      <Parts components={{ Text: () => <MarkdownText components={{ h1: Heading }} /> }} /></Runtime>)
    expect(screen.getByTestId('custom-heading')).toBeTruthy()
    expect(screen.getByText('A paragraph').closest('p')?.className).toContain('aui-md-p')
  })
  it('renders strikethrough through the real GFM plugin', () => {
    render(<Runtime text={'The ~~old~~ current answer'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByText('old').tagName).toBe('DEL')
    expect(screen.getByText(/current answer/)).toBeTruthy()
  })
  it('renders task list markers from GFM', () => {
    const { container } = render(<Runtime text={'- [x] Done\n- [ ] Next'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    const items = container.querySelectorAll('li')
    expect(items).toHaveLength(2)
    const checkboxes = container.querySelectorAll('input[type="checkbox"]')
    expect(checkboxes).toHaveLength(2)
    expect((checkboxes[0] as HTMLInputElement).checked).toBe(true)
    expect((checkboxes[1] as HTMLInputElement).checked).toBe(false)
  })
  it('keeps a table header separate from its data cells', () => {
    render(<Runtime text={'| Name | Status |\n| --- | --- |\n| Alpha | Running |'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    const name = screen.getByRole('columnheader', { name: 'Name' })
    const status = screen.getByRole('cell', { name: 'Running' })
    expect(name.className).toContain('aui-md-th')
    expect(status.className).toContain('aui-md-td')
    expect(name.closest('table')).toBe(status.closest('table'))
  })
  it('keeps ordered list numbering as an ordered list', () => {
    render(<Runtime text={'1. Gather\n2. Build\n3. Verify'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    const list = screen.getByRole('list')
    expect(list.tagName).toBe('OL')
    expect(list.className).toContain('aui-md-ol')
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })
  it('renders a fenced code block as one preformatted block', () => {
    const { container } = render(<Runtime text={'```python\nprint(1)\nprint(2)\n```'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    const pre = container.querySelector('pre.aui-md-pre')
    expect(pre).toBeTruthy()
    expect(pre?.textContent).toContain('print(1)')
    expect(pre?.textContent).toContain('print(2)')
    expect(screen.getByText('python')).toBeTruthy()
  })
  it('copies the code block through the real clipboard hook', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    clipboard(writeText)
    render(<Runtime text={'```ts\nconst live = true;\n```'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
      await Promise.resolve()
    })
    expect(writeText).toHaveBeenCalledWith('const live = true;\n')
  })
  it('does not claim a successful copy before a clipboard promise resolves', async () => {
    let resolveCopy!: () => void
    clipboard(() => new Promise<void>(resolve => { resolveCopy = resolve }))
    render(<Runtime text={'```ts\nconst live = true;\n```'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy()
    await act(async () => { resolveCopy(); await Promise.resolve() })
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy()
  })
  it('preserves a custom paragraph component across equal rerenders', () => {
    const Paragraph = ({ children }: { children?: ReactNode }) => <p data-testid="consumer-paragraph">{children}</p>
    function Custom() { return <MarkdownText components={{ p: Paragraph }} /> }
    const view = render(<Runtime text="Current answer"><Parts components={{ Text: Custom }} /></Runtime>)
    expect(screen.getByTestId('consumer-paragraph').textContent).toBe('Current answer')
    view.rerender(<Runtime text="Current answer"><Parts components={{ Text: Custom }} /></Runtime>)
    expect(screen.getByTestId('consumer-paragraph').textContent).toBe('Current answer')
  })
  it('does not turn raw HTML script text into an executable script', () => {
    const { container } = render(<Runtime text={'`<script>alert(1)</script>`'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getByText('<script>alert(1)</script>').tagName).toBe('CODE')
    expect(container.querySelector('script')).toBeNull()
  })
})

describe('Renderer data updates from a live caller', () => {
  it('changes the rendered Mermaid graph when caller code changes', async () => {
    const view = render(<BaseMermaid code={'graph TD\n A[First] --> B[End]'} />)
    await waitFor(() => expect(view.container.querySelector('[data-slot="mermaid-diagram"] svg')).toBeTruthy())
    const first = view.container.querySelector('[data-slot="mermaid-diagram"]')?.innerHTML
    view.rerender(<BaseMermaid code={'graph TD\n C[Second] --> D[End]'} />)
    await waitFor(() => expect(view.container.querySelector('[data-slot="mermaid-diagram"]')?.innerHTML).not.toBe(first))
    expect(view.container.querySelector('[data-slot="mermaid-diagram"]')?.innerHTML).toContain('Second')
  })
  it('changes from skeleton to an actual diagram after streaming completes', async () => {
    const view = render(<BaseMermaid code={validMermaid} streaming />)
    expect(view.container.querySelector('[data-slot="mermaid-skeleton"]')).toBeTruthy()
    view.rerender(<BaseMermaid code={validMermaid} streaming={false} />)
    await waitFor(() => expect(view.container.querySelector('[data-slot="mermaid-diagram"] svg')).toBeTruthy())
    expect(view.container.querySelector('[data-slot="mermaid-skeleton"]')).toBeNull()
  })
  it('changes from a failed parse to a real graph after caller correction', async () => {
    const view = render(<BaseMermaid code={invalidMermaid} />)
    expect(view.container.querySelector('[data-slot="mermaid-fallback"]')).toBeTruthy()
    view.rerender(<BaseMermaid code={validMermaid} />)
    await waitFor(() => expect(view.container.querySelector('[data-slot="mermaid-diagram"] svg')).toBeTruthy())
    expect(view.container.querySelector('[data-slot="mermaid-fallback"]')).toBeNull()
  })
  it('returns to the skeleton if the caller begins streaming again', () => {
    const view = render(<BaseMermaid code={validMermaid} />)
    expect(view.container.querySelector('[data-slot="mermaid-diagram"]')).toBeTruthy()
    view.rerender(<BaseMermaid code={validMermaid} streaming />)
    expect(view.container.querySelector('[data-slot="mermaid-skeleton"]')).toBeTruthy()
    expect(view.container.querySelector('[data-slot="mermaid-diagram"]')).toBeNull()
  })
  it('keeps a caller class on the rendered SVG surface', () => {
    const view = render(<BaseMermaid code={validMermaid} className="consumer-graph" />)
    const surface = view.container.querySelector('[data-slot="mermaid-diagram"]')
    expect(surface?.className).toContain('consumer-graph')
  })
  it('keeps the class on a parse fallback so layout remains stable', () => {
    const view = render(<BaseMermaid code={invalidMermaid} className="consumer-graph" />)
    const surface = view.container.querySelector('[data-slot="mermaid-fallback"]')
    expect(surface?.className).toContain('consumer-graph')
    expect(surface?.textContent).toContain(invalidMermaid)
  })
  it('passes Mermaid wrapper class to the real base component', () => {
    const view = render(<Runtime><AuiMermaid code={validMermaid} language="mermaid"
      className="consumer-wrapper" /></Runtime>)
    expect(view.container.querySelector('[data-slot="mermaid-diagram"]')?.className).toContain('consumer-wrapper')
  })
  it('exposes Mermaid zoom through the adapter export', () => {
    expect(AuiMermaid.Zoom).toBe(MermaidZoom)
    expect(AuiMermaid.displayName).toBe('MermaidDiagram')
    const view = render(<AuiMermaid.Zoom svg="<svg></svg>"><span>Preview</span></AuiMermaid.Zoom>)
    expect(view.getByText('Preview')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    expect(screen.getByRole('dialog', { name: 'Diagram' })).toBeTruthy()
  })
  it('keeps a Shiki plain-code container while switching from streaming', async () => {
    const view = render(<ShikiHighlighter code="const value = 1" language="typescript" streaming />)
    const container = view.container.querySelector('.aui-shiki-base')
    expect(container?.className).toContain('aui-shiki-streaming')
    view.rerender(<ShikiHighlighter code="const value = 1" language="typescript" streaming={false} />)
    await waitFor(() => expect(view.container.querySelector('.aui-shiki-base')).toBe(container))
    expect(view.container.textContent).toContain('const value = 1')
  })
  it('shows plain code when a Shiki language is unknown', async () => {
    const view = render(<ShikiHighlighter code="raw value" language="not-a-language" />)
    await waitFor(() => expect(view.container.textContent).toContain('raw value'))
    expect(view.container.querySelector('.aui-shiki-base')).toBeTruthy()
  })
  it('allows callers to change Shiki theme without changing their code', async () => {
    const view = render(<ShikiHighlighter code="const value = 1" language="typescript"
      theme={{ light: 'github-light-default', dark: 'github-dark-default' }} />)
    await waitFor(() => expect(view.container.textContent).toContain('const value = 1'))
    view.rerender(<ShikiHighlighter code="const value = 1" language="typescript"
      theme={{ light: 'github-light', dark: 'github-dark' }} />)
    await waitFor(() => expect(view.container.textContent).toContain('const value = 1'))
  })
  it('keeps a Prism code block when language changes from JS to Python', async () => {
    const view = render(<PrismHighlighter components={prismComponents} code="print(1)" language="js" />)
    await waitFor(() => expect(view.container.textContent).toContain('print(1)'))
    view.rerender(<PrismHighlighter components={prismComponents} code="print(1)" language="python" />)
    await waitFor(() => expect(view.container.textContent).toContain('print(1)'))
    expect(view.container.querySelectorAll('pre').length).toBeGreaterThanOrEqual(2)
  })
})

describe('Renderer boundaries and error handling', () => {
  it('does not enable code copy when no fenced code is present', () => {
    render(<Runtime text="Just a normal answer"><Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.queryByRole('button', { name: 'Copy' })).toBeNull()
    expect(screen.getByText('Just a normal answer')).toBeTruthy()
  })
  it('does not claim clipboard success if copying a fenced block fails', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('Clipboard denied'))
    clipboard(writeText)
    render(<Runtime text={'```js\nconst answer = 1;\n```'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
      await Promise.resolve()
    })
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy()
  })
  it('shows a separate copy button for separate code blocks', () => {
    render(<Runtime text={'```ts\nlet a = 1;\n```\n\n```python\nprint(2)\n```'}>
      <Parts components={{ Text: MarkdownText }} /></Runtime>)
    expect(screen.getAllByRole('button', { name: 'Copy' })).toHaveLength(2)
    expect(screen.getByText('ts')).toBeTruthy()
    expect(screen.getByText('python')).toBeTruthy()
  })
  it('keeps an error diagram as plain caller data without an expansion action', () => {
    const view = render(<BaseMermaid code={invalidMermaid} />)
    expect(view.container.querySelector('pre')?.textContent).toBe(invalidMermaid)
    expect(screen.queryByRole('button', { name: 'Expand diagram' })).toBeNull()
  })
  it('does not show an error for a streaming diagram while parse is deferred', () => {
    const view = render(<BaseMermaid code={invalidMermaid} streaming />)
    expect(view.container.querySelector('[data-slot="mermaid-skeleton"]')).toBeTruthy()
    expect(view.container.querySelector('[data-slot="mermaid-fallback"]')).toBeNull()
  })
  it('keeps a modal open on Tab while moving focus within controls', () => {
    render(<MermaidZoom svg="<svg></svg>"><div>Preview</div></MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const zoomIn = screen.getByRole('button', { name: 'Zoom in' })
    zoomIn.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(screen.getByRole('dialog', { name: 'Diagram' })).toBeTruthy()
  })
  it('keeps the original SVG unchanged while namespacing zoom references', () => {
    const svg = '<svg><path id="branch"/><use href="#branch"/></svg>'
    const view = render(<MermaidZoom svg={svg}><div data-testid="preview" dangerouslySetInnerHTML={{ __html: svg }} /></MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    expect(view.getByTestId('preview').innerHTML).toContain('id="branch"')
    expect(document.querySelector('[data-slot="mermaid-zoom-content"]')?.innerHTML).toContain('id="branch-zoom"')
  })
  it('handles an empty SVG in zoom without fabricating a diagram', () => {
    render(<MermaidZoom svg=""><span>Preview</span></MermaidZoom>)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    expect(document.querySelector('[data-slot="mermaid-zoom-content"]')?.innerHTML).toBe('')
    expect(screen.getByText('Preview')).toBeTruthy()
  })
  it('ignores an empty copy request after a previous successful copy', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    clipboard(writeText)
    const { result } = renderHook(() => useCopyToClipboard())
    await act(async () => {
      result.current.copyToClipboard('first')
      await Promise.resolve()
    })
    expect(result.current.isCopied).toBe(true)
    await act(async () => { result.current.copyToClipboard('') })
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(result.current.isCopied).toBe(true)
  })
  it('does not schedule a timer for a rejected clipboard operation', async () => {
    vi.useFakeTimers()
    const writeText = vi.fn().mockRejectedValue(new Error('Blocked'))
    clipboard(writeText)
    const { result } = renderHook(() => useCopyToClipboard({ copiedDuration: 100 }))
    await act(async () => {
      result.current.copyToClipboard('blocked')
      await Promise.resolve()
    })
    expect(vi.getTimerCount()).toBe(0)
    expect(result.current.isCopied).toBe(false)
  })
  it('keeps a streaming Shiki wrapper responsive to a changed code value', () => {
    const view = render(<ShikiHighlighter code="one" language="text" streaming />)
    expect(screen.getByText('one')).toBeTruthy()
    view.rerender(<ShikiHighlighter code="two" language="text" streaming />)
    expect(screen.getByText('two')).toBeTruthy()
    expect(screen.queryByText('one')).toBeNull()
  })
})

describe('Renderer accessibility and stable source wrappers', () => {
  it('labels an expanded diagram as a modal with reachable controls', () => {
    render(<BaseMermaid code={validMermaid} />)
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }))
    const dialog = screen.getByRole('dialog', { name: 'Diagram' })
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    for (const label of ['Zoom in', 'Zoom out', 'Reset zoom', 'Close']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }
  })
  it('does not steal keyboard focus before the diagram is opened', () => {
    render(<BaseMermaid code={validMermaid} />)
    const trigger = screen.getByRole('button', { name: 'Expand diagram' })
    expect(document.activeElement).not.toBe(trigger)
    trigger.focus()
    expect(document.activeElement).toBe(trigger)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('renders a changed code language in the existing Shiki container', () => {
    const view = render(<ShikiHighlighter code="const n = 1" language="typescript" streaming />)
    const holder = view.container.querySelector('.aui-shiki-base')
    view.rerender(<ShikiHighlighter code="const n = 1" language="javascript" streaming />)
    expect(view.container.querySelector('.aui-shiki-base')).toBe(holder)
    expect(view.container.textContent).toContain('const n = 1')
  })
})
