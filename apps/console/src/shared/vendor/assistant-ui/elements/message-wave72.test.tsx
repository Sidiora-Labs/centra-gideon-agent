import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState, type FormEvent } from 'react'
import { FeedbackDialog } from './feedback-dialog'
import { StoppedRun } from './stopped-run'
import { DaySeparator, type DatedMessage } from './day-separator'
import { SpeakerIdentity, type SpeakerTurn } from './speaker-identity'
import { RegenerateMenu, type RegenerateOption } from './regenerate-menu'
import { ConfidenceMarker, type ConfidenceClaim } from './confidence-marker'

afterEach(cleanup)

describe('FeedbackDialog', () => {
  it('renders the donor reason, note, and submission controls with real callbacks', () => {
    const toggle = vi.fn()
    const noteChange = vi.fn()
    const submit = vi.fn()
    const view = render(<FeedbackDialog reasons={['Incorrect answer', 'Missing source']}
      selected={['Incorrect answer']} note="" sent={false} onToggleReason={toggle}
      onNoteChange={noteChange} onSubmit={submit} data-testid="feedback" />)
    const root = view.getByTestId('feedback')
    expect(root.dataset.slot).toBe('feedback-dialog')
    const incorrect = within(root).getByRole('button', { name: 'Incorrect answer' })
    const missing = within(root).getByRole('button', { name: 'Missing source' })
    expect(incorrect.getAttribute('aria-pressed')).toBe('true')
    expect(missing.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(incorrect)
    fireEvent.click(missing)
    expect(toggle.mock.calls).toEqual([['Incorrect answer'], ['Missing source']])
    fireEvent.change(within(root).getByRole('textbox', { name: 'Anything else?' }),
      { target: { value: 'Citation missing on the second claim' } })
    expect(noteChange).toHaveBeenCalledTimes(1)
    expect(noteChange).toHaveBeenCalledWith('Citation missing on the second claim')
    fireEvent.click(within(root).getByRole('button', { name: 'Send feedback' }))
    expect(submit).toHaveBeenCalledOnce()
  })

  it('accepts a note-only downvote and lets a saving wrapper remove submit', () => {
    const update = vi.fn()
    const send = vi.fn()
    const view = render(<FeedbackDialog reasons={[]} selected={[]} note="First note"
      sent={false} onNoteChange={update} onSubmit={send} />)
    expect(view.container.querySelector('[data-slot="feedback-dialog"]')).not.toBeNull()
    expect(view.container.querySelectorAll('[aria-pressed]')).toHaveLength(0)
    const note = screen.getByRole('textbox', { name: 'Anything else?' }) as HTMLTextAreaElement
    expect(note.value).toBe('First note')
    fireEvent.change(note, { target: { value: 'Clarify the result' } })
    expect(update).toHaveBeenCalledTimes(1)
    expect(update).toHaveBeenCalledWith('Clarify the result')
    view.rerender(<fieldset disabled><FeedbackDialog reasons={[]} selected={[]}
      note="Clarify the result" sent={false} onNoteChange={update} /></fieldset>)
    expect(screen.queryByRole('button', { name: 'Send feedback' })).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Anything else?' }).matches(':disabled')).toBe(true)
    expect(send).not.toHaveBeenCalled()
  })

  it('shows static reasons and a read-only note when no actions are supplied', () => {
    const view = render(<FeedbackDialog reasons={['Other']} selected={['Other']}
      note="Saved explanation" sent={false} />)
    expect(screen.queryByRole('button', { name: 'Other' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Send feedback' })).toBeNull()
    const reason = screen.getByText('Other')
    expect(reason.tagName).toBe('SPAN')
    expect(reason.dataset.selected).toBe('true')
    const note = screen.getByRole('textbox', { name: 'Anything else?' }) as HTMLTextAreaElement
    expect(note.readOnly).toBe(true)
    expect(view.container.querySelector('[role="status"]')).not.toBeNull()
  })

  it('announces only confirmed submission and removes editable content', () => {
    const submit = vi.fn()
    const view = render(<FeedbackDialog reasons={['Other']} selected={[]}
      note="" sent={false} onSubmit={submit} />)
    const status = view.container.querySelector('[role="status"]') as HTMLElement
    expect(status.textContent).not.toContain('Feedback sent')
    view.rerender(<FeedbackDialog reasons={['Other']} selected={['Other']}
      note="real note" sent onSubmit={submit} />)
    expect(status.textContent).toContain('Feedback sent.')
    expect(status.textContent).not.toMatch(/tune the model/i)
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Send feedback' })).toBeNull()
    expect(submit).not.toHaveBeenCalled()
  })
})

describe('StoppedRun', () => {
  it('renders the actual partial words and supplied stop reason', () => {
    const view = render(<StoppedRun words={['Partial', 'answer']} reason="Stopped by user" />)
    const root = view.container.querySelector('[data-slot="stopped-run"]') as HTMLElement
    expect(root.textContent).toContain('Partial answer')
    expect(root.textContent).toContain('Stopped by user')
    expect(within(root).queryByRole('button')).toBeNull()
    expect(root.querySelectorAll('svg')).toHaveLength(1)
  })

  it('shows a persisted stop reason without repeating partial words when requested', () => {
    const words = ['Already', 'rendered', 'above']
    const view = render(<StoppedRun words={words} reason="Stopped by user" showWords={false} />)
    const root = view.container.querySelector('[data-slot="stopped-run"]') as HTMLElement
    expect(root.querySelector('p')).toBeNull()
    expect(root.textContent).toContain('Stopped by user')
    expect(root.textContent).not.toContain(words.join(' '))
    expect(within(root).queryByRole('button')).toBeNull()
    view.rerender(<StoppedRun words={words} reason="Stopped by user" />)
    expect(root.querySelector('p')?.textContent).toContain(words.join(' '))
    expect(root.querySelector('p span[aria-hidden]')).not.toBeNull()
    view.rerender(<StoppedRun words={words} reason="Stopped by user" showWords />)
    expect(root.querySelector('p')?.textContent).toContain(words.join(' '))
  })

  it('offers only the continuation action when continuation exists', () => {
    const continueRun = vi.fn()
    render(<StoppedRun words={['Draft']} reason="Interrupted" onContinue={continueRun} />)
    const button = screen.getByRole('button', { name: 'Continue' })
    expect(screen.queryByRole('button', { name: 'Discard' })).toBeNull()
    fireEvent.click(button)
    expect(continueRun).toHaveBeenCalledOnce()
  })

  it('offers only discard when available and keeps callbacks distinct', () => {
    const discard = vi.fn()
    const continueRun = vi.fn()
    const view = render(<StoppedRun words={[]} reason="Limit reached"
      onDiscard={discard} onContinue={continueRun} />)
    expect(view.container.querySelector('[data-slot="stopped-run"] p')?.textContent).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(discard).toHaveBeenCalledOnce()
    expect(continueRun).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(continueRun).toHaveBeenCalledOnce()
  })
})

describe('DaySeparator', () => {
  const messages: DatedMessage[] = [
    { id: 'm1', day: 'Today', time: '09:00', role: 'user', text: 'Morning' },
    { id: 'm2', day: 'Today', time: '09:01', role: 'assistant', text: 'Hello' },
    { id: 'm3', day: 'Yesterday', time: '17:45', role: 'user', text: 'Earlier' },
  ]

  it('groups consecutive messages by supplied day in chronological order', () => {
    const view = render(<DaySeparator messages={messages} data-testid="days" />)
    const root = view.getByTestId('days')
    expect(root.dataset.slot).toBe('day-separator')
    expect(within(root).getAllByText('Today')).toHaveLength(1)
    expect(within(root).getAllByText('Yesterday')).toHaveLength(1)
    expect(root.textContent?.indexOf('Morning')).toBeLessThan(root.textContent?.indexOf('Hello') ?? 0)
    expect(root.textContent?.indexOf('Hello')).toBeLessThan(root.textContent?.indexOf('Earlier') ?? 0)
    expect(within(root).getByText('09:00')).not.toBeNull()
    expect(within(root).getByText('17:45')).not.toBeNull()
  })

  it('starts a new divider when the same day returns later in supplied order', () => {
    const reordered = [...messages, { id: 'm4', day: 'Today', time: '18:00', role: 'assistant' as const, text: 'Return' }]
    render(<DaySeparator messages={reordered} />)
    expect(screen.getAllByText('Today')).toHaveLength(2)
    expect(screen.getByText('Return')).not.toBeNull()
    expect(screen.getByText('18:00')).not.toBeNull()
  })

  it('keeps user and assistant alignment distinct and handles an empty list', () => {
    const view = render(<DaySeparator messages={messages} className="custom-day" />)
    expect(view.container.firstElementChild?.classList.contains('custom-day')).toBe(true)
    expect(screen.getByText('Morning').parentElement?.className).toContain('flex-row-reverse')
    expect(screen.getByText('Hello').parentElement?.className).not.toContain('flex-row-reverse')
    view.rerender(<DaySeparator messages={[]} />)
    expect(view.container.querySelector('[data-slot="day-separator"]')?.children).toHaveLength(0)
  })
})

describe('SpeakerIdentity', () => {
  const turns: SpeakerTurn[] = [
    { id: 'u', kind: 'user', name: 'You', text: 'Can you check?' },
    { id: 'a', kind: 'agent', name: 'Gideon', detail: 'primary', text: 'Checking.' },
    { id: 's', kind: 'subagent', name: 'Research', detail: 'delegated', text: 'Found two sources.' },
    { id: 't', kind: 'tool', name: 'Search', text: '2 results' },
  ]

  it('renders role, identity, detail, and actual turn text for all four kinds', () => {
    const view = render(<SpeakerIdentity turns={turns} data-testid="speakers" />)
    const root = view.getByTestId('speakers')
    expect(root.dataset.slot).toBe('speaker-identity')
    for (const turn of turns) {
      expect(within(root).getByText(turn.name)).not.toBeNull()
      expect(within(root).getByText(turn.text)).not.toBeNull()
    }
    expect(within(root).getByText('primary')).not.toBeNull()
    expect(within(root).getByText('delegated')).not.toBeNull()
    expect(root.querySelectorAll('svg')).toHaveLength(4)
  })

  it('distinguishes delegated agents visually without inventing missing detail', () => {
    const view = render(<SpeakerIdentity turns={turns} />)
    const delegated = screen.getByText('Research').closest('div.flex') as HTMLElement
    expect(delegated?.previousElementSibling?.className).toContain('rounded-full')
    const user = screen.getByText('You').closest('div.flex') as HTMLElement
    expect(user?.previousElementSibling?.className).not.toContain('rounded-full')
    expect(view.container.textContent).not.toContain('undefined')
  })

  it('renders no invented speaker or message when no turns are supplied', () => {
    const view = render(<SpeakerIdentity turns={[]} className="custom-speaker" />)
    const root = view.container.querySelector('[data-slot="speaker-identity"]') as HTMLElement
    expect(root.classList.contains('custom-speaker')).toBe(true)
    expect(root.children).toHaveLength(0)
    expect(root.textContent).toBe('')
  })
})

describe('RegenerateMenu', () => {
  const options: RegenerateOption[] = [
    { id: 'standard', label: 'Try again', detail: 'same settings' },
    { id: 'alternate', label: 'Use alternate', detail: 'available option' },
  ]

  it('opens via caller state and passes the picked option id', () => {
    const change = vi.fn()
    const pick = vi.fn()
    const view = render(<RegenerateMenu options={options} open={false} currentId="standard"
      onOpenChange={change} onPick={pick} />)
    const toggle = screen.getByRole('button', { name: 'Regenerate response options' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('button', { name: /Use alternate/ })).toBeNull()
    fireEvent.click(toggle)
    expect(change).toHaveBeenCalledTimes(1)
    expect(change).toHaveBeenCalledWith(true)
    view.rerender(<RegenerateMenu options={options} open currentId="standard"
      onOpenChange={change} onPick={pick} />)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('current')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Use alternate/ }))
    expect(pick).toHaveBeenCalledTimes(1)
    expect(pick).toHaveBeenCalledWith('alternate')
  })

  it('requests closing without silently changing its controlled open state', () => {
    const change = vi.fn()
    render(<RegenerateMenu options={options} open currentId="alternate" onOpenChange={change} />)
    const toggle = screen.getByRole('button', { name: 'Regenerate response options' })
    fireEvent.click(toggle)
    expect(change).toHaveBeenCalledTimes(1)
    expect(change).toHaveBeenCalledWith(false)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('current')).not.toBeNull()
  })

  it('shows static options without an actionable pick callback', () => {
    const view = render(<RegenerateMenu options={options} open currentId="standard" />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('Try again').parentElement?.tagName).toBe('DIV')
    expect(screen.getByText('Use alternate').parentElement?.tagName).toBe('DIV')
    expect(screen.getByText('available option')).not.toBeNull()
    expect(view.container.querySelector('[data-slot="regenerate-menu"]')).not.toBeNull()
  })

  it('handles no available options and does not claim a model change', () => {
    const change = vi.fn()
    const view = render(<RegenerateMenu options={[]} open currentId=""
      onOpenChange={change} className="custom-regenerate" />)
    expect(view.container.firstElementChild?.classList.contains('custom-regenerate')).toBe(true)
    expect(screen.queryByText('current')).toBeNull()
    expect(screen.queryByRole('button', { name: /model/i })).toBeNull()
    expect(screen.getByRole('button', { name: 'Regenerate response options' })).not.toBeNull()
  })
})

describe('ConfidenceMarker', () => {
  const claims: ConfidenceClaim[] = [
    { id: 'c1', text: 'Documented fact', confidence: 'grounded', basis: 'project log' },
    { id: 'c2', text: 'Likely consequence', confidence: 'inferred', basis: 'available evidence' },
    { id: 'c3', text: 'Open question', confidence: 'uncertain', basis: 'no source yet' },
  ]

  it('shows only the hovered claim basis and reports focus changes to the caller', () => {
    const hover = vi.fn()
    const view = render(<ConfidenceMarker claims={claims} hoveredId="" onHover={hover} />)
    expect(view.container.querySelector('[data-slot="confidence-marker"]')).not.toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
    const grounded = screen.getByRole('button', { name: 'Documented fact' })
    fireEvent.mouseEnter(grounded)
    expect(hover).toHaveBeenCalledWith('c1')
    view.rerender(<ConfidenceMarker claims={claims} hoveredId="c1" onHover={hover} />)
    const status = screen.getByRole('status')
    expect(status.textContent).toContain('from a source · project log')
    expect(grounded.getAttribute('aria-describedby')).toBe(status.id)
    fireEvent.mouseLeave(grounded)
    expect(hover).toHaveBeenLastCalledWith('')
    fireEvent.focus(screen.getByRole('button', { name: 'Open question' }))
    expect(hover).toHaveBeenLastCalledWith('c3')
    fireEvent.blur(screen.getByRole('button', { name: 'Open question' }))
    expect(hover).toHaveBeenLastCalledWith('')
  })

  it('discloses inferred and uncertain basis without changing supplied claims', () => {
    const view = render(<ConfidenceMarker claims={claims} hoveredId="c2" onHover={() => {}} />)
    expect(screen.getByRole('status').textContent).toContain('inferred · available evidence')
    view.rerender(<ConfidenceMarker claims={claims} hoveredId="c3" onHover={() => {}} />)
    expect(screen.getByRole('status').textContent).toContain('unverified · no source yet')
    expect(claims.map((claim) => claim.confidence)).toEqual(['grounded', 'inferred', 'uncertain'])
  })

  it('renders static claims instead of inert buttons without a hover callback', () => {
    const view = render(<ConfidenceMarker claims={claims} hoveredId="" />)
    expect(screen.queryByRole('button')).toBeNull()
    for (const claim of claims) {
      const element = screen.getByText(claim.text)
      expect(element.tagName).toBe('SPAN')
      expect(element.className).toContain('underline')
    }
    expect(view.container.querySelectorAll('[aria-describedby]')).toHaveLength(0)
  })

  it('keeps supplied class names and avoids basis disclosure for unknown ids', () => {
    const view = render(<ConfidenceMarker claims={claims} hoveredId="missing"
      onHover={() => {}} className="custom-confidence" />)
    expect(view.container.firstElementChild?.classList.contains('custom-confidence')).toBe(true)
    expect(screen.queryByRole('status')).toBeNull()
    expect(view.container.querySelectorAll('[aria-describedby]')).toHaveLength(0)
    view.rerender(<ConfidenceMarker claims={[]} hoveredId="missing" />)
    expect(view.container.querySelector('[data-slot="confidence-marker"] p')?.textContent).toBe('')
  })
})

describe('message components in controlled consumer flows', () => {
  it('round-trips multiple feedback reasons and a note before reporting submission', () => {
    const submissions: Array<{ reasons: string[]; note: string }> = []
    function FeedbackFlow() {
      const [selected, setSelected] = useState<string[]>([])
      const [note, setNote] = useState('')
      const [sent, setSent] = useState(false)
      const toggle = (reason: string) => setSelected((current) => current.includes(reason)
        ? current.filter((item) => item !== reason) : [...current, reason])
      return <FeedbackDialog reasons={['Wrong detail', 'Missing context', 'Wrong tone']}
        selected={selected} note={note} sent={sent} onToggleReason={toggle}
        onNoteChange={setNote} onSubmit={() => {
          submissions.push({ reasons: selected, note })
          setSent(true)
        }} />
    }
    render(<FeedbackFlow />)
    const wrong = screen.getByRole('button', { name: 'Wrong detail' })
    const context = screen.getByRole('button', { name: 'Missing context' })
    fireEvent.click(wrong)
    fireEvent.click(context)
    expect(wrong.getAttribute('aria-pressed')).toBe('true')
    expect(context.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(wrong)
    expect(wrong.getAttribute('aria-pressed')).toBe('false')
    fireEvent.change(screen.getByRole('textbox', { name: 'Anything else?' }),
      { target: { value: 'The second paragraph omits the customer constraint.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }))
    expect(submissions).toEqual([{ reasons: ['Missing context'],
      note: 'The second paragraph omits the customer constraint.' }])
    expect(screen.getByRole('status').textContent).toContain('Feedback sent.')
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('lets a consumer cancel and reopen feedback without submitting a stale note', () => {
    const submit = vi.fn()
    function DismissibleFeedback() {
      const [open, setOpen] = useState(true)
      const [note, setNote] = useState('')
      const toggle = () => {
        if (open) setNote('')
        setOpen(!open)
      }
      return <>
        <button type="button" onClick={toggle}>{open ? 'Cancel feedback' : 'Reopen feedback'}</button>
        {open && <FeedbackDialog reasons={[]} selected={[]} note={note} sent={false}
          onNoteChange={setNote} onSubmit={submit} />}
      </>
    }
    render(<DismissibleFeedback />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Anything else?' }),
      { target: { value: 'A draft I changed my mind about' } })
    expect((screen.getByRole('textbox', { name: 'Anything else?' }) as HTMLTextAreaElement).value)
      .toBe('A draft I changed my mind about')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel feedback' }))
    expect(screen.queryByRole('textbox', { name: 'Anything else?' })).toBeNull()
    expect(submit).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Reopen feedback' }))
    expect((screen.getByRole('textbox', { name: 'Anything else?' }) as HTMLTextAreaElement).value).toBe('')
    expect(screen.getByRole('button', { name: 'Send feedback' })).not.toBeNull()
  })

  it('keeps feedback controls from submitting a surrounding form by accident', () => {
    const formSubmit = vi.fn((event: FormEvent<HTMLFormElement>) => event.preventDefault())
    const feedbackSubmit = vi.fn()
    const toggleReason = vi.fn()
    render(<form onSubmit={formSubmit}>
      <FeedbackDialog reasons={['Needs correction']} selected={[]} note="" sent={false}
        onToggleReason={toggleReason} onSubmit={feedbackSubmit} />
    </form>)
    const reason = screen.getByRole('button', { name: 'Needs correction' }) as HTMLButtonElement
    const send = screen.getByRole('button', { name: 'Send feedback' }) as HTMLButtonElement
    expect(reason.type).toBe('button')
    expect(send.type).toBe('button')
    fireEvent.click(reason)
    fireEvent.click(send)
    expect(toggleReason).toHaveBeenCalledWith('Needs correction')
    expect(feedbackSubmit).toHaveBeenCalledTimes(1)
    expect(formSubmit).not.toHaveBeenCalled()
  })

  it('isolates feedback callbacks when two independent messages are visible', () => {
    const firstSubmit = vi.fn()
    const secondSubmit = vi.fn()
    const firstNote = vi.fn()
    const secondNote = vi.fn()
    render(<>
      <FeedbackDialog data-testid="first-feedback" reasons={[]} selected={[]}
        note="first" sent={false} onNoteChange={firstNote} onSubmit={firstSubmit} />
      <FeedbackDialog data-testid="second-feedback" reasons={[]} selected={[]}
        note="second" sent={false} onNoteChange={secondNote} onSubmit={secondSubmit} />
    </>)
    const second = screen.getByTestId('second-feedback')
    fireEvent.change(within(second).getByRole('textbox', { name: 'Anything else?' }),
      { target: { value: 'Only the second message needs correction' } })
    fireEvent.click(within(second).getByRole('button', { name: 'Send feedback' }))
    expect(secondNote).toHaveBeenCalledWith('Only the second message needs correction')
    expect(secondSubmit).toHaveBeenCalledTimes(1)
    expect(firstNote).not.toHaveBeenCalled()
    expect(firstSubmit).not.toHaveBeenCalled()
    expect((within(screen.getByTestId('first-feedback')).getByRole('textbox') as HTMLTextAreaElement).value)
      .toBe('first')
  })

  it('reveals stopped-run actions only when the matching operation becomes available', () => {
    const continueRun = vi.fn()
    const discardRun = vi.fn()
    const view = render(<StoppedRun words={['The', 'answer', 'started']} reason="Paused" />)
    expect(screen.queryByRole('button')).toBeNull()
    view.rerender(<StoppedRun words={['The', 'answer', 'started']} reason="Paused"
      onContinue={continueRun} />)
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(continueRun).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Discard' })).toBeNull()
    view.rerender(<StoppedRun words={['The', 'answer', 'started']} reason="Stopped"
      onDiscard={discardRun} />)
    expect(screen.queryByRole('button', { name: 'Continue' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(discardRun).toHaveBeenCalledTimes(1)
    expect(continueRun).toHaveBeenCalledTimes(1)
    expect(view.container.textContent).toContain('Stopped')
  })

  it('routes controls to the correct stopped run when two are visible', () => {
    const continueFirst = vi.fn()
    const discardSecond = vi.fn()
    render(<>
      <StoppedRun data-testid="stopped-first" words={['First', 'partial']}
        reason="User paused" onContinue={continueFirst} />
      <StoppedRun data-testid="stopped-second" words={['Second', 'partial']}
        reason="Request cancelled" onDiscard={discardSecond} />
    </>)
    const first = screen.getByTestId('stopped-first')
    const second = screen.getByTestId('stopped-second')
    expect(within(first).getByText(/First partial/)).not.toBeNull()
    expect(within(second).getByText(/Second partial/)).not.toBeNull()
    expect(within(first).queryByRole('button', { name: 'Discard' })).toBeNull()
    expect(within(second).queryByRole('button', { name: 'Continue' })).toBeNull()
    fireEvent.click(within(second).getByRole('button', { name: 'Discard' }))
    expect(discardSecond).toHaveBeenCalledTimes(1)
    expect(continueFirst).not.toHaveBeenCalled()
    fireEvent.click(within(first).getByRole('button', { name: 'Continue' }))
    expect(continueFirst).toHaveBeenCalledTimes(1)
  })

  it('inserts new day dividers only when appended message data crosses a day', () => {
    const first: DatedMessage = { id: 'turn-1', day: 'Monday', time: '23:58', role: 'user', text: 'Late note' }
    const second: DatedMessage = { id: 'turn-2', day: 'Monday', time: '23:59', role: 'assistant', text: 'Response' }
    const third: DatedMessage = { id: 'turn-3', day: 'Tuesday', time: '00:02', role: 'assistant', text: 'Follow-up' }
    const view = render(<DaySeparator messages={[first]} />)
    expect(screen.getAllByText('Monday')).toHaveLength(1)
    view.rerender(<DaySeparator messages={[first, second]} />)
    expect(screen.getAllByText('Monday')).toHaveLength(1)
    expect(screen.getByText('23:59')).not.toBeNull()
    view.rerender(<DaySeparator messages={[first, second, third]} />)
    expect(screen.getAllByText('Monday')).toHaveLength(1)
    expect(screen.getAllByText('Tuesday')).toHaveLength(1)
    const content = view.container.textContent ?? ''
    expect(content.indexOf('Late note')).toBeLessThan(content.indexOf('Response'))
    expect(content.indexOf('Response')).toBeLessThan(content.indexOf('Follow-up'))
    expect(screen.getByText('Follow-up').parentElement?.className).not.toContain('flex-row-reverse')
  })

  it('renders message text literally without treating it as markup or an action', () => {
    const messages: DatedMessage[] = [
      { id: 'literal-1', day: 'Today', time: '10:00', role: 'user', text: '<script>alert(1)</script>' },
      { id: 'literal-2', day: 'Today', time: '10:01', role: 'assistant', text: '<button>approve</button>' },
    ]
    const view = render(<DaySeparator messages={messages} />)
    expect(screen.getByText('<script>alert(1)</script>')).not.toBeNull()
    expect(screen.getByText('<button>approve</button>')).not.toBeNull()
    expect(view.container.querySelector('script')).toBeNull()
    expect(screen.queryByRole('button', { name: 'approve' })).toBeNull()
    expect(screen.getByText('10:00')).not.toBeNull()
    expect(screen.getByText('10:01')).not.toBeNull()
  })

  it('uses supplied speaker identities and role tones without fabricated details', () => {
    const turns: SpeakerTurn[] = [
      { id: 'turn-u', kind: 'user', name: 'Ari', text: 'Please inspect this.' },
      { id: 'turn-a', kind: 'agent', name: 'Gideon', detail: 'answer', text: 'Checking.' },
      { id: 'turn-s', kind: 'subagent', name: 'Research worker', text: 'Two sources found.' },
      { id: 'turn-t', kind: 'tool', name: 'Search', detail: '2 results', text: 'Completed.' },
    ]
    const view = render(<SpeakerIdentity turns={turns} />)
    expect(screen.getByText('Ari').closest('div.flex')?.previousElementSibling?.className).toContain('text-foreground/55')
    expect(screen.getByText('Gideon').closest('div.flex')?.previousElementSibling?.className).toContain('text-blue-600')
    expect(screen.getByText('Research worker').closest('div.flex')?.previousElementSibling?.className).toContain('rounded-full')
    expect(screen.getByText('Search').closest('div.flex')?.previousElementSibling?.className).toContain('text-foreground/40')
    expect(view.container.textContent).not.toContain('undefined')
    expect(screen.getByText('answer')).not.toBeNull()
    expect(screen.getByText('2 results')).not.toBeNull()
    expect(view.container.querySelectorAll('svg')).toHaveLength(4)
  })

  it('updates speaker order and text from new turn data without keeping stale rows', () => {
    const early: SpeakerTurn = { id: 'early', kind: 'user', name: 'User', text: 'First question' }
    const reply: SpeakerTurn = { id: 'reply', kind: 'agent', name: 'Gideon', text: 'First answer' }
    const view = render(<SpeakerIdentity turns={[early, reply]} />)
    expect(view.container.textContent?.indexOf('First question')).toBeLessThan(
      view.container.textContent?.indexOf('First answer') ?? 0)
    const updated: SpeakerTurn = { ...reply, text: 'Corrected answer', detail: 'revised' }
    view.rerender(<SpeakerIdentity turns={[updated]} />)
    expect(screen.queryByText('First question')).toBeNull()
    expect(screen.queryByText('First answer')).toBeNull()
    expect(screen.getByText('Corrected answer')).not.toBeNull()
    expect(screen.getByText('revised')).not.toBeNull()
    expect(view.container.querySelectorAll('svg')).toHaveLength(1)
  })

  it('keeps speaker names and details as text when external data contains markup', () => {
    const turns: SpeakerTurn[] = [{ id: 'unsafe', kind: 'tool', name: '<img src=x>',
      detail: '<script>bad()</script>', text: '<a href="javascript:bad()">Open</a>' }]
    const view = render(<SpeakerIdentity turns={turns} />)
    expect(screen.getByText('<img src=x>')).not.toBeNull()
    expect(screen.getByText('<script>bad()</script>')).not.toBeNull()
    expect(screen.getByText('<a href="javascript:bad()">Open</a>')).not.toBeNull()
    expect(view.container.querySelector('img')).toBeNull()
    expect(view.container.querySelector('script')).toBeNull()
    expect(view.container.querySelector('a')).toBeNull()
  })

  it('lets a controlled regenerate menu pick a real option and mark it current', () => {
    const picked: string[] = []
    const options: RegenerateOption[] = [
      { id: 'same', label: 'Retry response', detail: 'current setting' },
      { id: 'brief', label: 'Try a brief response', detail: 'shorter answer' },
    ]
    function RegenerationFlow() {
      const [open, setOpen] = useState(false)
      const [currentId, setCurrentId] = useState('same')
      return <RegenerateMenu options={options} open={open} currentId={currentId}
        onOpenChange={setOpen} onPick={(id) => {
          picked.push(id)
          setCurrentId(id)
          setOpen(false)
        }} />
    }
    render(<RegenerationFlow />)
    const toggle = screen.getByRole('button', { name: 'Regenerate response options' })
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: /Try a brief response/ }))
    expect(picked).toEqual(['brief'])
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: /Try a brief response/ }).textContent).toContain('current')
    expect(screen.getByRole('button', { name: /Retry response/ }).textContent).toContain('current setting')
  })

  it('uses stable option ids when two regenerate choices share a visible label', () => {
    const pick = vi.fn()
    const options: RegenerateOption[] = [
      { id: 'provider-a', label: 'Retry', detail: 'first available route' },
      { id: 'provider-b', label: 'Retry', detail: 'second available route' },
    ]
    const view = render(<RegenerateMenu options={options} open currentId="provider-a" onPick={pick} />)
    const choices = screen.getAllByRole('button', { name: /Retry/ })
    expect(choices).toHaveLength(2)
    expect(choices[0].textContent).toContain('current')
    expect(choices[1].textContent).toContain('second available route')
    fireEvent.click(choices[1])
    expect(pick).toHaveBeenCalledWith('provider-b')
    expect(pick).toHaveBeenCalledTimes(1)
    expect(view.container.querySelector('[data-slot="regenerate-menu"]')).not.toBeNull()
  })

  it('keeps regenerate choices noninteractive until a pick callback exists', () => {
    const options: RegenerateOption[] = [{ id: 'retry', label: 'Retry answer', detail: 'same settings' }]
    const pick = vi.fn()
    const view = render(<RegenerateMenu options={options} open currentId="retry" />)
    expect(screen.getByText('Retry answer').parentElement?.tagName).toBe('DIV')
    expect(screen.queryByRole('button')).toBeNull()
    view.rerender(<RegenerateMenu options={options} open currentId="retry" onPick={pick} />)
    const choice = screen.getByRole('button', { name: /Retry answer/ })
    expect(choice.getAttribute('type')).toBe('button')
    fireEvent.click(choice)
    expect(pick).toHaveBeenCalledWith('retry')
    expect(pick).toHaveBeenCalledTimes(1)
  })

  it('moves confidence disclosure between real claims as focus changes', () => {
    const claims: ConfidenceClaim[] = [
      { id: 'source', text: 'Quoted passage', confidence: 'grounded', basis: 'record 42' },
      { id: 'estimate', text: 'Estimated effect', confidence: 'inferred', basis: 'observed trend' },
      { id: 'unknown', text: 'Open issue', confidence: 'uncertain', basis: 'not checked' },
    ]
    function ConfidenceFlow() {
      const [hoveredId, setHoveredId] = useState('')
      return <ConfidenceMarker claims={claims} hoveredId={hoveredId} onHover={setHoveredId} />
    }
    render(<ConfidenceFlow />)
    const source = screen.getByRole('button', { name: 'Quoted passage' })
    const estimate = screen.getByRole('button', { name: 'Estimated effect' })
    const unknown = screen.getByRole('button', { name: 'Open issue' })
    fireEvent.focus(source)
    expect(screen.getByRole('status').textContent).toContain('from a source · record 42')
    expect(source.getAttribute('aria-describedby')).toBe(screen.getByRole('status').id)
    fireEvent.focus(estimate)
    expect(screen.getByRole('status').textContent).toContain('inferred · observed trend')
    expect(source.getAttribute('aria-describedby')).toBeNull()
    fireEvent.mouseEnter(unknown)
    expect(screen.getByRole('status').textContent).toContain('unverified · not checked')
    fireEvent.mouseLeave(unknown)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('shows an externally selected confidence basis without inventing an action', () => {
    const claim: ConfidenceClaim = { id: 'cited', text: 'Supported observation',
      confidence: 'grounded', basis: 'signed transcript' }
    const view = render(<ConfidenceMarker claims={[claim]} hoveredId="cited" />)
    const text = screen.getByText('Supported observation')
    expect(text.tagName).toBe('SPAN')
    expect(screen.queryByRole('button')).toBeNull()
    const status = screen.getByRole('status')
    expect(status.textContent).toContain('signed transcript')
    expect(text.getAttribute('aria-describedby')).toBe(status.id)
    expect(view.container.querySelectorAll('svg')).toHaveLength(0)
  })

  it('escapes confidence claim text and evidence supplied by a remote source', () => {
    const claim: ConfidenceClaim = { id: 'remote', text: '<script>not markup</script>',
      confidence: 'uncertain', basis: '<img src=x onerror=alert(1)>' }
    const view = render(<ConfidenceMarker claims={[claim]} hoveredId="remote" onHover={() => {}} />)
    expect(screen.getByRole('button', { name: '<script>not markup</script>' })).not.toBeNull()
    expect(screen.getByRole('status').textContent).toContain('<img src=x onerror=alert(1)>')
    expect(view.container.querySelector('script')).toBeNull()
    expect(view.container.querySelector('img')).toBeNull()
    expect(view.container.querySelectorAll('[aria-describedby]')).toHaveLength(1)
  })
})
