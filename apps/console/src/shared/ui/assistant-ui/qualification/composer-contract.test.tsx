import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  Composer, ComposerBar, ComposerToolbar, ComposerActions, ComposerAttachButton,
  ComposerSend, ComposerModelTrigger, ComposerModelItem, ComposerContext,
  ComposerAttachmentChip, ComposerVoice, ComposerVoiceButton, ComposerInput,
  ComposerMenu, ComposerMenuItem, ComposerCommandItem, ComposerPersonItem,
  ComposerAttachments, applyMention,
} from '../../../vendor/assistant-ui/elements/composer'
import { MobileComposer } from '../../../vendor/assistant-ui/elements/mobile-composer'
import { SearchIcon } from 'lucide-react'

describe('static composer primitive contract', () => {
  it('keeps each caller-owned slot and its content in order', () => {
    const { container } = render(
      <Composer data-testid="composer"><ComposerBar dragActive><ComposerToolbar>
        <ComposerActions><ComposerAttachButton onClick={() => {}} /></ComposerActions>
        <ComposerSend streaming={false} idle={false} />
      </ComposerToolbar></ComposerBar></Composer>,
    )
    expect(screen.getByTestId('composer')).toHaveAttribute('data-slot', 'composer')
    expect(container.querySelector('[data-slot="composer-bar"]')).toHaveAttribute('data-drag-active', 'true')
    expect(container.querySelector('[data-slot="composer-toolbar"]')).toBeInTheDocument()
    expect(container.querySelector('[data-slot="composer-actions"]')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add attachment' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Send message' })).toBeEnabled()
  })

  it('disables attachment action when no handler exists', () => {
    render(<ComposerAttachButton />)
    expect(screen.getByRole('button', { name: 'Add attachment' })).toBeDisabled()
  })

  it('switches send control to stop during streaming', () => {
    const { rerender } = render(<ComposerSend streaming={false} idle />)
    expect(screen.getByRole('button', { name: 'Send message' })).toBeInTheDocument()
    rerender(<ComposerSend streaming idle={false} />)
    expect(screen.getByRole('button', { name: 'Stop generating' })).toBeInTheDocument()
  })

  it('passes selected model and expanded state to the trigger', () => {
    const { rerender } = render(<ComposerModelTrigger model="Fast" open={false} />)
    expect(screen.getByRole('button', { name: 'Fast' })).toHaveAttribute('aria-expanded', 'false')
    rerender(<ComposerModelTrigger model="Deep" open />)
    expect(screen.getByRole('button', { name: 'Deep' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('marks the selected model and preserves provided metadata', () => {
    const { rerender } = render(<ComposerModelItem entry={{ name: 'Alpha', meta: 'local' }} selected />)
    expect(screen.getByRole('button', { name: /Alphalocal/ })).toHaveAttribute('data-active', 'true')
    rerender(<ComposerModelItem entry={{ name: 'Alpha', meta: 'local' }} selected={false} />)
    expect(screen.getByRole('button', { name: /Alphalocal/ })).not.toHaveAttribute('data-active')
  })

  it('shows actual context segments and warning threshold', () => {
    const { container, rerender } = render(<ComposerContext usage={{ system: 1, tools: 1, messages: 3, total: 10 }} />)
    expect(screen.getByText('5k / 10k')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    const root = container.querySelector('[data-slot="composer-context"]') as HTMLElement
    expect(root.querySelectorAll('[style*="width"]')).toHaveLength(3)
    rerender(<ComposerContext usage={{ system: 2, tools: 3, messages: 5, total: 10 }} />)
    expect(within(root).getByText('100%')).toHaveClass('text-red-500')
  })

  it('shows a measured zero percent without inventing a breakdown', () => {
    render(<ComposerContext measured={{ percent: 0, usedTokens: 0, windowTokens: 128000 }} />)
    expect(screen.getByRole('button', { name: 'Context: 0% used' })).toBeInTheDocument()
    expect(screen.getByText('0 / 128,000 tokens')).toBeInTheDocument()
    expect(screen.getByText('Breakdown unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/System/)).toBeNull()
  })

  it('keeps unknown measured usage unknown', () => {
    render(<ComposerContext measured={{ percent: null, usedTokens: null, windowTokens: null, breakdown: null }} />)
    expect(screen.getByRole('button', { name: 'Context: unknown' })).toBeInTheDocument()
    expect(screen.getByText('Breakdown unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/tokens/)).toBeNull()
  })

  it('shows a measured window without claiming measured use', () => {
    const { rerender } = render(<ComposerContext measured={{ percent: null, usedTokens: null, windowTokens: 8192 }} />)
    expect(screen.getByRole('button', { name: 'Context: unknown' })).toBeInTheDocument()
    expect(screen.getByText('Window: 8,192 tokens')).toBeInTheDocument()
    expect(screen.getByText('Breakdown unavailable')).toBeInTheDocument()
    expect(screen.queryByText('0 / 8,192 tokens')).toBeNull()
    rerender(<ComposerContext measured={{ percent: null, usedTokens: 41, windowTokens: null }} />)
    expect(screen.getByText('Used: 41 tokens')).toBeInTheDocument()
    expect(screen.queryByText(/Window:/)).toBeNull()
  })

  it('labels a partial measured breakdown without summing missing buckets', () => {
    render(<ComposerContext measured={{ percent: null, usedTokens: 820, windowTokens: 1000, breakdownComplete: false,
      breakdown: [{ label: 'Input', tokens: 610, tint: '#123456' }, { label: 'Cache read', tokens: 100, tint: '#654321' }] }} />)
    expect(screen.getByText('820 / 1,000 tokens')).toBeInTheDocument()
    expect(screen.getByText('610 tokens')).toBeInTheDocument()
    expect(screen.getByText('100 tokens')).toBeInTheDocument()
    expect(screen.getByText('Breakdown incomplete')).toBeInTheDocument()
    expect(screen.queryByText('710 / 1,000 tokens')).toBeNull()
  })

  it('renders only supplied measured breakdown and sources', () => {
    render(<ComposerContext measured={{ percent: 37.8, breakdown: [{ label: 'Messages', tokens: 4200, tint: '#123456' }], sources: ['runtime meter'] }} />)
    expect(screen.getByRole('button', { name: 'Context: 38% used' })).toBeInTheDocument()
    expect(screen.getByText('4,200 tokens')).toBeInTheDocument()
    expect(screen.getByText('Sources: runtime meter')).toBeInTheDocument()
    expect(screen.queryByText('Breakdown unavailable')).toBeNull()
  })

  it('handles a zero total without invalid segment widths', () => {
    const { container } = render(<ComposerContext usage={{ system: 0, tools: 0, messages: 0, total: 0 }} />)
    expect(screen.getByText('0%')).toBeInTheDocument()
    expect(container.querySelectorAll('[style="width: 0%;"]')).toHaveLength(3)
  })

  it('shows upload progress and allows removing completed attachments', () => {
    const onRemove = vi.fn()
    const { container, rerender } = render(<ComposerAttachmentChip attachment={{ name: 'file.txt', meta: '20 KB', state: 'uploading', progress: 45 }} onRemove={onRemove} />)
    expect(container.querySelector('[data-slot="composer-attachment"]')).toHaveAttribute('data-state', 'uploading')
    expect(container.querySelector('[style="width: 45%;"]')).toBeInTheDocument()
    rerender(<ComposerAttachmentChip attachment={{ name: 'file.txt', meta: '20 KB', state: 'done' }} onRemove={onRemove} />)
    fireEvent.click(screen.getByRole('button', { name: 'Remove file.txt' }))
    expect(onRemove).toHaveBeenCalledTimes(1)
    expect(onRemove).toHaveBeenCalledWith('file.txt')
  })

  it('renders read-only and failed attachments without action buttons', () => {
    const { rerender } = render(<ComposerAttachmentChip attachment={{ name: 'done', meta: 'Ready', state: 'done', kind: 'archive' }} />)
    expect(screen.queryByRole('button')).toBeNull()
    rerender(<ComposerAttachmentChip attachment={{ name: 'bad', meta: 'Upload failed', state: 'error', kind: 'image' }} />)
    expect(screen.getByText('Upload failed')).toHaveClass('text-red-600/80')
  })

  it('changes the voice action and waveform with recording state', () => {
    const { container, rerender } = render(<ComposerVoice recording={false} seconds={0} />)
    expect(screen.getByText('Transcribing')).toBeInTheDocument()
    expect(container.querySelectorAll('[aria-hidden] span')).toHaveLength(14)
    rerender(<ComposerVoice recording seconds={5} />)
    expect(screen.getByText('0:05')).toBeInTheDocument()
    expect(container.querySelector('[data-recording="true"]')).toBeInTheDocument()
  })

  it('switches accessible voice action with active state', () => {
    const { rerender } = render(<ComposerVoiceButton active={false} />)
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeInTheDocument()
    rerender(<ComposerVoiceButton active />)
    expect(screen.getByRole('button', { name: 'Stop recording' })).toBeInTheDocument()
  })

  it('calls submit on Enter but respects a prevented key event', () => {
    const onSubmit = vi.fn()
    const { rerender } = render(<ComposerInput onSubmit={onSubmit} />)
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
    rerender(<ComposerInput onSubmit={onSubmit} onKeyDown={event => event.preventDefault()} />)
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('renders menu choices and a selected command', () => {
    render(<ComposerMenu open><ComposerMenuItem active>Open</ComposerMenuItem><ComposerCommandItem command={{ name: 'find', description: 'Search files', icon: SearchIcon }} active /></ComposerMenu>)
    expect(screen.getByRole('button', { name: 'Open' })).toHaveAttribute('data-active', 'true')
    expect(screen.getByRole('button', { name: /findSearch files/ })).toBeInTheDocument()
  })

  it('renders a person option and replaces trailing mention text', () => {
    render(<ComposerPersonItem person={{ name: 'Alex', role: 'agent' }} active={false} />)
    expect(screen.getByRole('button', { name: /Alexagent/ })).toBeInTheDocument()
    expect(applyMention('Ask @al', 'Alex')).toBe('Ask @Alex ')
    expect(applyMention('No mention', 'Alex')).toBe('No mention')
  })

  it.each(['file', 'knowledge', 'prompt'] as const)('shows %s mention kind and subordinate metadata', role => {
    render(<ComposerPersonItem person={{ name: 'Found item', role }} meta={<span>42 KB · current project</span>} active />)
    const row = screen.getByRole('button', { name: /Found item/ })
    expect(row).toHaveTextContent(role)
    expect(row).toHaveTextContent('42 KB · current project')
    expect(row).toHaveAttribute('data-active', 'true')
  })

  it('preserves human and agent rows without metadata', () => {
    const { rerender } = render(<ComposerPersonItem person={{ name: 'Ada', role: 'human' }} active={false} />)
    expect(screen.getByRole('button', { name: /Ada/ })).toHaveTextContent('human')
    rerender(<ComposerPersonItem person={{ name: 'Bot', role: 'agent' }} active={false} />)
    expect(screen.getByRole('button', { name: /Bot/ })).toHaveTextContent('agent')
  })

  it('mounts the attachment collection once', () => {
    const { container } = render(<ComposerAttachments><span>report.pdf</span></ComposerAttachments>)
    expect(container.querySelectorAll('[data-slot="composer-attachments"]')).toHaveLength(1)
    expect(screen.getByText('report.pdf')).toBeInTheDocument()
  })
})

describe('mobile composer caller-owned editor', () => {
  const base = { value: '', keyboardOpen: false, running: false, actions: ['Search'] }

  it('keeps a single editor and exposes action, attach, send controls', () => {
    const onAction = vi.fn()
    const onAttach = vi.fn()
    const onSend = vi.fn()
    render(<MobileComposer {...base} value="hello" editor={<textarea aria-label="Gideon editor" />} onAction={onAction} onAttach={onAttach} onSend={onSend} />)
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add an attachment' }))
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onAction).toHaveBeenCalledTimes(1)
    expect(onAction).toHaveBeenCalledWith('Search')
    expect(onAttach).toHaveBeenCalledTimes(1)
    expect(onSend).toHaveBeenCalledTimes(1)
  })

  it('suppresses an incorrect return-key hint for a caller-owned mobile editor', () => {
    const { rerender } = render(<MobileComposer {...base} keyboardOpen keyboardHint={null} editor={<textarea aria-label="Gideon editor" />} />)
    expect(screen.queryByText('return to send')).toBeNull()
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    rerender(<MobileComposer {...base} keyboardOpen keyboardHint="Use Send" editor={<textarea aria-label="Gideon editor" />} />)
    expect(screen.getByText('Use Send')).toBeInTheDocument()
  })

  it('hides action chips while keyboard is open and shows stop during a run', () => {
    const onStop = vi.fn()
    render(<MobileComposer {...base} keyboardOpen running onStop={onStop} />)
    expect(screen.queryByRole('button', { name: 'Search' })).toBeNull()
    expect(screen.getByText('return to send')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(onStop).toHaveBeenCalledTimes(1)
  })
})
