import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { ElicitationForm, type ElicitationField, type ElicitationState } from './elicitation-form'
import { McpServerPanel, type McpServer } from './mcp-server-panel'
import { ToolError } from './tool-error'
import { PermissionGrant, type GrantScope } from './permission-grant'
import { ComputerUse, type ComputerStep } from './computer-use'
import { CodeRunner, type RunState } from './code-runner'

afterEach(cleanup)

const fields: ElicitationField[] = [
  { name: 'branch', label: 'Branch name', value: 'release', kind: 'text', required: true },
  { name: 'region', label: 'Region', value: 'EU', kind: 'choice', options: ['US', 'EU', 'APAC'] },
  { name: 'notify', label: 'Notify owner', value: 'true', kind: 'toggle' },
]
const servers: McpServer[] = [
  { id: 'calendar', name: 'Calendar', transport: 'stdio', status: 'connected', tools: ['list', 'create'] },
  { id: 'drive', name: 'Drive', transport: 'http', status: 'needs-auth', tools: ['search'] },
  { id: 'mail', name: 'Mail', transport: 'sse', status: 'connecting', tools: [] },
  { id: 'code', name: 'Code', transport: 'stdio', status: 'failed', tools: ['run'] },
]
const steps: ComputerStep[] = [
  { id: 'a', action: 'Click', target: 'Menu', x: 10, y: 20 },
  { id: 'b', action: 'Type', target: 'Search', x: 45, y: 60 },
  { id: 'c', action: 'Click', target: 'Result', x: 80, y: 15 },
  { id: 'd', action: 'Scroll', target: 'Page', x: 90, y: 90 },
]

function elicitation(state: ElicitationState = 'request', itemFields = fields, onAccept = vi.fn(), onDecline = vi.fn()) {
  const view = render(<ElicitationForm server="Git MCP" message="Choose a branch" fields={itemFields}
    state={state} onAccept={onAccept} onDecline={onDecline} data-testid="request-card" />)
  return { ...view, onAccept, onDecline }
}

function grant(scope: GrantScope | 'pending' = 'pending', onGrant = vi.fn()) {
  const view = render(<PermissionGrant capability="Read calendar" requester="Planner"
    reach={['Events', 'Calendars']} scope={scope} onGrant={onGrant} data-testid="grant-card" />)
  return { ...view, onGrant }
}

describe('ElicitationForm donor behavior', () => {
  it('shows the requesting server, message, supplied values and required field marker', () => {
    elicitation()
    const card = screen.getByTestId('request-card')
    expect(card.dataset.slot).toBe('elicitation-form')
    expect(within(card).getByText('Git MCP')).toBeTruthy()
    expect(within(card).getByText('Choose a branch')).toBeTruthy()
    expect(within(card).getByText('Branch name')).toBeTruthy()
    expect(within(card).getByText('release')).toBeTruthy()
    expect(within(card).getByText('*')).toBeTruthy()
    expect(within(card).getByText('needs input')).toBeTruthy()
  })
  it('shows each choice and distinguishes the selected value', () => {
    elicitation()
    const us = screen.getByText('US')
    const eu = screen.getByText('EU')
    const apac = screen.getByText('APAC')
    expect(us.className).toContain('text-foreground/55')
    expect(eu.className).toContain('bg-foreground')
    expect(apac.className).toContain('text-foreground/55')
  })
  it('shows enabled toggle state as On without inventing an editable control', () => {
    elicitation()
    expect(screen.getByText('On')).toBeTruthy()
    expect(screen.queryByRole('switch')).toBeNull()
  })
  it('shows disabled toggle state as Off', () => {
    elicitation('request', [{ name: 'notify', label: 'Notify', kind: 'toggle', value: 'false' }])
    expect(screen.getByText('Off')).toBeTruthy()
    expect(screen.queryByText('On')).toBeNull()
  })
  it('supports a choice field without options without rendering a fake selection', () => {
    elicitation('request', [{ name: 'region', label: 'Region', kind: 'choice', value: 'EU' }])
    expect(screen.getByText('Region')).toBeTruthy()
    expect(screen.queryByText('EU')).toBeNull()
  })
  it('sends acceptance through the caller supplied action once', () => {
    const { onAccept, onDecline } = elicitation()
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onAccept).toHaveBeenCalledTimes(1)
    expect(onDecline).not.toHaveBeenCalled()
  })
  it('sends decline through the distinct caller supplied action once', () => {
    const { onAccept, onDecline } = elicitation()
    fireEvent.click(screen.getByRole('button', { name: 'Decline' }))
    expect(onDecline).toHaveBeenCalledTimes(1)
    expect(onAccept).not.toHaveBeenCalled()
  })
  it('updates to accepted state after a real parent state transition', () => {
    function Controlled() {
      const [state, setState] = useState<ElicitationState>('request')
      return <ElicitationForm server="Git MCP" message="Choose" fields={fields} state={state}
        onAccept={() => setState('accepted')} onDecline={() => setState('declined')} />
    }
    render(<Controlled />)
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(screen.getByText('Sent to Git MCP')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Decline' })).toBeNull()
  })
  it('updates to declined state after a real parent state transition', () => {
    function Controlled() {
      const [state, setState] = useState<ElicitationState>('request')
      return <ElicitationForm server="Git MCP" message="Choose" fields={fields} state={state}
        onAccept={() => setState('accepted')} onDecline={() => setState('declined')} />
    }
    render(<Controlled />)
    fireEvent.click(screen.getByRole('button', { name: 'Decline' }))
    expect(screen.getByText('Declined')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
  })
  it('accepts custom host properties and class without losing the donor surface', () => {
    const { container } = render(<ElicitationForm server="Git" message="Choose" fields={[]}
      state="request" className="consumer-class" title="Review request" />)
    const card = container.querySelector('[data-slot="elicitation-form"]')!
    expect(card.className).toContain('consumer-class')
    expect(card.className).toContain('rounded-[20px]')
    expect(card.getAttribute('title')).toBe('Review request')
  })
})

describe('McpServerPanel donor behavior', () => {
  it('counts only connected servers and displays each server with its tool count', () => {
    render(<McpServerPanel servers={servers} />)
    expect(screen.getByText('1 of 4 connected')).toBeTruthy()
    expect(screen.getByText('Calendar')).toBeTruthy()
    expect(screen.getByText('Drive')).toBeTruthy()
    expect(screen.getByText('Mail')).toBeTruthy()
    expect(screen.getByText('Code')).toBeTruthy()
    expect(screen.getAllByText('1 tools')).toHaveLength(2)
    expect(screen.getByText('2 tools')).toBeTruthy()
    expect(screen.getByText('0 tools')).toBeTruthy()
  })
  it('keeps rows noninteractive when no toggle action is supplied', () => {
    const { container } = render(<McpServerPanel servers={servers} />)
    expect(container.querySelector('[data-slot="mcp-server-panel"]')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Calendar/ })).toBeNull()
    expect(screen.queryByText('stdio')).toBeNull()
  })
  it('shows the current expansion and every real tool name', () => {
    render(<McpServerPanel servers={servers} expandedId="calendar" onToggle={vi.fn()} />)
    const row = screen.getByRole('button', { name: /Calendar/ })
    expect(row.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('stdio')).toBeTruthy()
    expect(screen.getByText('list')).toBeTruthy()
    expect(screen.getByText('create')).toBeTruthy()
    expect(screen.queryByText('search')).toBeNull()
  })
  it('passes the clicked stable server ID to the parent toggle action', () => {
    const onToggle = vi.fn()
    render(<McpServerPanel servers={servers} onToggle={onToggle} />)
    fireEvent.click(screen.getByRole('button', { name: /Drive/ }))
    expect(onToggle).toHaveBeenCalledExactlyOnceWith('drive')
    expect(screen.getByRole('button', { name: /Drive/ }).getAttribute('aria-expanded')).toBe('false')
  })
  it('can expand after a parent update and collapse the same server', () => {
    function Controlled() {
      const [expandedId, setExpandedId] = useState<string>()
      return <McpServerPanel servers={servers} expandedId={expandedId}
        onToggle={id => setExpandedId(current => current === id ? undefined : id)} />
    }
    render(<Controlled />)
    fireEvent.click(screen.getByRole('button', { name: /Calendar/ }))
    expect(screen.getByText('list')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Calendar/ }))
    expect(screen.queryByText('list')).toBeNull()
  })
  it('authorizes only a needs-auth row when that row is expanded', () => {
    const onAuthorize = vi.fn()
    render(<McpServerPanel servers={servers} expandedId="drive" onAuthorize={onAuthorize} />)
    expect(screen.getByText('needs auth')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Authorize' }))
    expect(onAuthorize).toHaveBeenCalledExactlyOnceWith('drive')
  })
  it('does not offer authorization for a connected, connecting or failed row', () => {
    for (const server of servers.filter(item => item.status !== 'needs-auth')) {
      const view = render(<McpServerPanel servers={[server]} expandedId={server.id} onAuthorize={vi.fn()} />)
      expect(screen.queryByRole('button', { name: 'Authorize' })).toBeNull()
      view.unmount()
    }
  })
  it('does not present an authorization control without an action', () => {
    render(<McpServerPanel servers={servers} expandedId="drive" />)
    expect(screen.queryByRole('button', { name: 'Authorize' })).toBeNull()
    expect(screen.getByText('search')).toBeTruthy()
  })
  it('shows distinct status labels for the four typed states', () => {
    render(<McpServerPanel servers={servers} onToggle={vi.fn()} />)
    for (const label of ['connected', 'connecting', 'needs auth', 'failed']) {
      expect(screen.getByText(label)).toBeTruthy()
    }
  })
  it('handles an empty server collection without a control or false count', () => {
    render(<McpServerPanel servers={[]} />)
    expect(screen.getByText('0 of 0 connected')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('ToolError donor behavior', () => {
  const props = { name: 'web_fetch', target: 'https://docs.example/page', message: 'Request timed out',
    attempt: 2, maxAttempts: 3, retrying: false }
  it('shows operation, target, error and attempt budget without changing their values', () => {
    const { container } = render(<ToolError {...props} />)
    expect(container.querySelector('[data-slot="tool-error"]')).toBeTruthy()
    expect(screen.getByText('web_fetch')).toBeTruthy()
    expect(screen.getByText('https://docs.example/page')).toBeTruthy()
    expect(screen.getByText('Request timed out')).toBeTruthy()
    expect(screen.getByText('2/3')).toBeTruthy()
  })
  it('invokes retry only when the retry button is activated', () => {
    const onRetry = vi.fn()
    const onSkip = vi.fn()
    render(<ToolError {...props} onRetry={onRetry} onSkip={onSkip} />)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(onSkip).not.toHaveBeenCalled()
  })
  it('invokes skip through the separate supplied action', () => {
    const onRetry = vi.fn()
    const onSkip = vi.fn()
    render(<ToolError {...props} onRetry={onRetry} onSkip={onSkip} />)
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(onSkip).toHaveBeenCalledTimes(1)
    expect(onRetry).not.toHaveBeenCalled()
  })
  it('disables skip when no skip action exists', () => {
    render(<ToolError {...props} />)
    expect((screen.getByRole('button', { name: 'Skip' }) as HTMLButtonElement).disabled).toBe(true)
  })
  it('disables retry while retrying and labels the in-flight state', () => {
    const onRetry = vi.fn()
    render(<ToolError {...props} retrying onRetry={onRetry} />)
    const button = screen.getByRole('button', { name: 'Retrying' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.click(button)
    expect(onRetry).not.toHaveBeenCalled()
  })
  it('reacts to a real parent retry transition', () => {
    function Controlled() {
      const [retrying, setRetrying] = useState(false)
      return <ToolError {...props} retrying={retrying} onRetry={() => setRetrying(true)} />
    }
    render(<Controlled />)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(screen.getByRole('button', { name: 'Retrying' })).toBeTruthy()
  })
  it('allows host data and class attributes for result dispatch placement', () => {
    const { container } = render(<ToolError {...props} data-run-id="run-7" className="trace-card" />)
    const card = container.querySelector('[data-slot="tool-error"]')!
    expect(card.getAttribute('data-run-id')).toBe('run-7')
    expect(card.className).toContain('trace-card')
  })
})

describe('PermissionGrant donor behavior', () => {
  it('names the capability and requester and displays the exact scope of access', () => {
    grant()
    expect(screen.getByText('Read calendar')).toBeTruthy()
    expect(screen.getByText('requested by Planner')).toBeTruthy()
    expect(screen.getByText('Events')).toBeTruthy()
    expect(screen.getByText('Calendars')).toBeTruthy()
    expect(screen.getByText('this grants')).toBeTruthy()
  })
  it('dispatches denied scope with no grant of access', () => {
    const { onGrant } = grant()
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    expect(onGrant).toHaveBeenCalledExactlyOnceWith('denied')
  })
  it('dispatches session scope distinctly', () => {
    const { onGrant } = grant()
    fireEvent.click(screen.getByRole('button', { name: 'This session' }))
    expect(onGrant).toHaveBeenCalledExactlyOnceWith('session')
  })
  it('dispatches persistent scope distinctly', () => {
    const { onGrant } = grant()
    fireEvent.click(screen.getByRole('button', { name: 'Always' }))
    expect(onGrant).toHaveBeenCalledExactlyOnceWith('always')
  })
  it('updates the pending decision to session grant after caller state changes', () => {
    function Controlled() {
      const [scope, setScope] = useState<GrantScope | 'pending'>('pending')
      return <PermissionGrant capability="Read calendar" requester="Planner" reach={['Events']}
        scope={scope} onGrant={setScope} />
    }
    render(<Controlled />)
    fireEvent.click(screen.getByRole('button', { name: 'This session' }))
    expect(screen.getByText('granted · session')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Always' })).toBeNull()
  })
  it('updates pending decision to denial without showing granted', () => {
    function Controlled() {
      const [scope, setScope] = useState<GrantScope | 'pending'>('pending')
      return <PermissionGrant capability="Read calendar" requester="Planner" reach={[]}
        scope={scope} onGrant={setScope} />
    }
    render(<Controlled />)
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    expect(screen.getByText('denied')).toBeTruthy()
    expect(screen.queryByText(/granted/)).toBeNull()
  })
  it('renders pending as status text when no decision callback was supplied', () => {
    render(<PermissionGrant capability="Read" requester="Planner" reach={[]} scope="pending" />)
    expect(screen.getByText('pending')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
  it.each<GrantScope>(['always', 'session', 'denied'])('shows persisted %s scope without action buttons', scope => {
    grant(scope)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText(scope === 'denied' ? 'denied' : `granted · ${scope}`)).toBeTruthy()
  })
  it('preserves custom host attributes on the permission surface', () => {
    const { container } = render(<PermissionGrant capability="Read" requester="Planner" reach={[]}
      scope="pending" data-testid="custom-grant" className="consumer-grant" />)
    const card = container.querySelector('[data-slot="permission-grant"]')!
    expect(card.getAttribute('data-testid')).toBe('custom-grant')
    expect(card.className).toContain('consumer-grant')
  })
})

describe('ComputerUse donor behavior', () => {
  it('renders a remote page as supplied children without fabricating a screenshot', () => {
    const { container } = render(<ComputerUse url="https://app.example" steps={[]} activeIndex={0}>
      <img alt="Actual browser frame" src="data:image/png;base64,aGVsbG8=" />
    </ComputerUse>)
    expect(container.querySelector('[data-slot="computer-use"]')).toBeTruthy()
    expect(screen.getByText('https://app.example')).toBeTruthy()
    expect(screen.getByAltText('Actual browser frame')).toBeTruthy()
    expect(screen.queryByText(/\d+\/\d+/)).toBeNull()
  })
  it('shows the active first action and exact first coordinate', () => {
    const { container } = render(<ComputerUse url="https://app.example" steps={steps} activeIndex={0}>
      <span>Frame</span>
    </ComputerUse>)
    expect(screen.getByText('Click')).toBeTruthy()
    expect(screen.getByText('Menu')).toBeTruthy()
    expect(screen.getByText('1/4')).toBeTruthy()
    const cursor = container.querySelector('svg[style*="left"]') as SVGElement
    expect(cursor.getAttribute('style')).toContain('left: 10%')
    expect(cursor.getAttribute('style')).toContain('top: 20%')
  })
  it('shows a bounded trail around the active third action', () => {
    const { container } = render(<ComputerUse url="https://app.example" steps={steps} activeIndex={2}>
      <span>Frame</span>
    </ComputerUse>)
    expect(screen.getByText('Click')).toBeTruthy()
    expect(screen.getByText('Result')).toBeTruthy()
    expect(screen.getByText('3/4')).toBeTruthy()
    expect(container.querySelectorAll('span[style*="opacity"]')).toHaveLength(3)
  })
  it('drops older trail marks as the fourth action becomes active', () => {
    const { container } = render(<ComputerUse url="https://app.example" steps={steps} activeIndex={3}>
      <span>Frame</span>
    </ComputerUse>)
    const marks = [...container.querySelectorAll('span[style*="opacity"]')]
    expect(marks).toHaveLength(3)
    expect(marks[0]?.getAttribute('style')).toContain('left: 45%')
    expect(marks[2]?.getAttribute('style')).toContain('left: 90%')
    expect(screen.getByText('Scroll')).toBeTruthy()
  })
  it('clamps a negative active index to an in-range step', () => {
    render(<ComputerUse url="https://app.example" steps={steps} activeIndex={-5}>
      <span>Frame</span>
    </ComputerUse>)
    expect(screen.getByText('1/4')).toBeTruthy()
  })
  it('clamps an oversized active index to the final in-range step', () => {
    render(<ComputerUse url="https://app.example" steps={steps} activeIndex={99}>
      <span>Frame</span>
    </ComputerUse>)
    expect(screen.getByText('4/4')).toBeTruthy()
    expect(screen.getByText('Page')).toBeTruthy()
  })
  it('responds to active index changes from the caller', () => {
    function Controlled() {
      const [index, setIndex] = useState(0)
      return <><button onClick={() => setIndex(1)}>Next step</button>
        <ComputerUse url="https://app.example" steps={steps} activeIndex={index}><span>Frame</span></ComputerUse></>
    }
    render(<Controlled />)
    expect(screen.getByText('1/4')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next step' }))
    expect(screen.getByText('2/4')).toBeTruthy()
    expect(screen.getByText('Search')).toBeTruthy()
  })
})

describe('CodeRunner donor behavior', () => {
  const base = { language: 'typescript', code: 'console.log(1)', output: [] as string[] }
  it('shows language, code and run affordance in idle state without output', () => {
    const { container } = render(<CodeRunner {...base} state="idle" onRun={vi.fn()} />)
    expect(container.querySelector('[data-slot="code-runner"]')).toBeTruthy()
    expect(screen.getByText('typescript')).toBeTruthy()
    expect(screen.getByText('console.log(1)')).toBeTruthy()
    expect(screen.queryByText('output')).toBeNull()
    expect((screen.getByRole('button', { name: 'Run this snippet' }) as HTMLButtonElement).disabled).toBe(false)
  })
  it('calls the supplied run action once with no synthetic output', () => {
    const onRun = vi.fn()
    render(<CodeRunner {...base} state="idle" onRun={onRun} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run this snippet' }))
    expect(onRun).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('output')).toBeNull()
  })
  it('disables run during execution and shows streamed output lines', () => {
    const onRun = vi.fn()
    render(<CodeRunner {...base} state="running" output={['first', 'second']} onRun={onRun} />)
    expect((screen.getByRole('button', { name: 'Run this snippet' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('output')).toBeTruthy()
    expect(screen.getByText('first')).toBeTruthy()
    expect(screen.getByText('second')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Run this snippet' }))
    expect(onRun).not.toHaveBeenCalled()
  })
  it('shows successful output and duration after execution', () => {
    render(<CodeRunner {...base} state="ok" output={['1', 'done']} durationMs={42} />)
    expect(screen.getByText('42ms')).toBeTruthy()
    expect(screen.getByText('1')).toBeTruthy()
    expect(screen.getByText('done')).toBeTruthy()
    expect(screen.getByText('done').className).toContain('text-foreground/70')
  })
  it('shows error output with an error surface', () => {
    render(<CodeRunner {...base} state="error" output={['TypeError: failed']} durationMs={7} />)
    expect(screen.getByText('7ms')).toBeTruthy()
    expect(screen.getByText('TypeError: failed').className).toContain('text-red-600')
  })
  it('does not show duration while running even if a stale duration is supplied', () => {
    render(<CodeRunner {...base} state="running" durationMs={20} />)
    expect(screen.queryByText('20ms')).toBeNull()
  })
  it('reacts to a parent run transition and retains the caller code', () => {
    function Controlled() {
      const [state, setState] = useState<RunState>('idle')
      return <CodeRunner {...base} state={state} output={state === 'running' ? ['Starting'] : []}
        onRun={() => setState('running')} />
    }
    render(<Controlled />)
    fireEvent.click(screen.getByRole('button', { name: 'Run this snippet' }))
    expect(screen.getByText('Starting')).toBeTruthy()
    expect(screen.getByText('console.log(1)')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Run this snippet' }) as HTMLButtonElement).disabled).toBe(true)
  })
  it('passes through attributes and host class for placement in Gideon result cards', () => {
    const { container } = render(<CodeRunner {...base} state="idle" className="consumer-runner" data-testid="runner" />)
    const card = container.querySelector('[data-slot="code-runner"]')!
    expect(card.className).toContain('consumer-runner')
    expect(card.getAttribute('data-testid')).toBe('runner')
  })
})

describe('Tool use components under changing caller data', () => {
  it('keeps elicitation choices synchronized with a changed field value', () => {
    const initial = [{ name: 'region', label: 'Region', kind: 'choice', value: 'EU', options: ['EU', 'US'] }] as const
    const view = render(<ElicitationForm server="Git" message="Choose region" fields={initial}
      state="request" onAccept={vi.fn()} />)
    expect(screen.getByText('EU').className).toContain('bg-foreground')
    expect(screen.getByText('US').className).not.toContain('bg-foreground text-background')
    view.rerender(<ElicitationForm server="Git" message="Choose region"
      fields={[{ ...initial[0], value: 'US' }]} state="request" onAccept={vi.fn()} />)
    expect(screen.getByText('US').className).toContain('bg-foreground')
    expect(screen.getByText('EU').className).not.toContain('bg-foreground text-background')
  })
  it('keeps toggles synchronized with a changed true/false result', () => {
    const view = render(<ElicitationForm server="Git" message="Choose"
      fields={[{ name: 'notify', label: 'Notify', kind: 'toggle', value: 'true' }]}
      state="request" onAccept={vi.fn()} />)
    expect(screen.getByText('On')).toBeTruthy()
    view.rerender(<ElicitationForm server="Git" message="Choose"
      fields={[{ name: 'notify', label: 'Notify', kind: 'toggle', value: 'false' }]}
      state="request" onAccept={vi.fn()} />)
    expect(screen.getByText('Off')).toBeTruthy()
    expect(screen.queryByText('On')).toBeNull()
  })
  it('keeps already accepted requests final after a server name update', () => {
    const view = render(<ElicitationForm server="Git" message="Choose" fields={[]}
      state="accepted" onAccept={vi.fn()} onDecline={vi.fn()} />)
    expect(screen.getByText('Sent to Git')).toBeTruthy()
    view.rerender(<ElicitationForm server="Drive" message="Choose" fields={[]}
      state="accepted" onAccept={vi.fn()} onDecline={vi.fn()} />)
    expect(screen.getByText('Sent to Drive')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
  })
  it('keeps decline final even if accept callback remains supplied', () => {
    const accept = vi.fn()
    const decline = vi.fn()
    render(<ElicitationForm server="Git" message="Choose" fields={[]}
      state="declined" onAccept={accept} onDecline={decline} />)
    expect(screen.getByText('Declined')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Decline' })).toBeNull()
    expect(accept).not.toHaveBeenCalled()
    expect(decline).not.toHaveBeenCalled()
  })
  it('never exposes active elicitation actions without caller handlers', () => {
    render(<ElicitationForm server="Git" message="Choose" fields={[]} state="request" />)
    const accept = screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement
    const decline = screen.getByRole('button', { name: 'Decline' }) as HTMLButtonElement
    expect(accept.disabled).toBe(true)
    expect(decline.disabled).toBe(true)
  })
  it('disables only the missing elicitation action if the other is supplied', () => {
    const onAccept = vi.fn()
    render(<ElicitationForm server="Git" message="Choose" fields={[]} state="request" onAccept={onAccept} />)
    expect((screen.getByRole('button', { name: 'Decline' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onAccept).toHaveBeenCalledTimes(1)
  })
  it('preserves values containing punctuation as text rather than markup', () => {
    const { container } = render(<ElicitationForm server="Git" message="Review <branch>"
      fields={[{ name: 'branch', label: 'Branch', kind: 'text', value: '<script>alert(1)</script>' }]}
      state="request" onAccept={vi.fn()} />)
    expect(screen.getByText('<script>alert(1)</script>')).toBeTruthy()
    expect(container.querySelector('script')).toBeNull()
  })
  it('distinguishes required and optional fields without adding a second marker', () => {
    const view = render(<ElicitationForm server="Git" message="Review"
      fields={[{ name: 'a', label: 'Required', kind: 'text', value: 'x', required: true },
        { name: 'b', label: 'Optional', kind: 'text', value: 'y', required: false }]}
      state="request" onAccept={vi.fn()} />)
    expect(within(view.container).getAllByText('*')).toHaveLength(1)
    expect(screen.getByText('Required')).toBeTruthy()
    expect(screen.getByText('Optional')).toBeTruthy()
  })
})

describe('MCP server connection and authorization transitions', () => {
  it('updates the connected count when a server becomes connected', () => {
    const view = render(<McpServerPanel servers={servers} />)
    expect(screen.getByText('1 of 4 connected')).toBeTruthy()
    const next: McpServer[] = servers.map(server => server.id === 'drive'
      ? { ...server, status: 'connected' } : server)
    view.rerender(<McpServerPanel servers={next} />)
    expect(screen.getByText('2 of 4 connected')).toBeTruthy()
    expect(screen.queryByText('1 of 4 connected')).toBeNull()
  })
  it('removes authorization when an expanded server completes authorization', () => {
    const view = render(<McpServerPanel servers={servers} expandedId="drive" onAuthorize={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Authorize' })).toBeTruthy()
    const next: McpServer[] = servers.map(server => server.id === 'drive'
      ? { ...server, status: 'connected' } : server)
    view.rerender(<McpServerPanel servers={next} expandedId="drive" onAuthorize={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Authorize' })).toBeNull()
    expect(screen.getByText('2 of 4 connected')).toBeTruthy()
  })
  it('keeps authorization hidden if a needs-auth row is collapsed', () => {
    render(<McpServerPanel servers={servers} expandedId="calendar" onAuthorize={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Authorize' })).toBeNull()
    expect(screen.getByText('list')).toBeTruthy()
    expect(screen.queryByText('search')).toBeNull()
  })
  it('counts an inserted connected server without changing existing IDs', () => {
    const newServer: McpServer = { id: 'notes', name: 'Notes', transport: 'http', status: 'connected', tools: ['read'] }
    render(<McpServerPanel servers={[...servers, newServer]} onToggle={vi.fn()} />)
    expect(screen.getByText('2 of 5 connected')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Notes/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Calendar/ })).toBeTruthy()
  })
  it('passes each distinct clicked ID to the same parent callback', () => {
    const onToggle = vi.fn()
    render(<McpServerPanel servers={servers} onToggle={onToggle} />)
    fireEvent.click(screen.getByRole('button', { name: /Calendar/ }))
    fireEvent.click(screen.getByRole('button', { name: /Code/ }))
    expect(onToggle.mock.calls).toEqual([['calendar'], ['code']])
  })
  it('updates visible tool list when the expanded ID changes', () => {
    const onToggle = vi.fn()
    const view = render(<McpServerPanel servers={servers} expandedId="calendar" onToggle={onToggle} />)
    expect(screen.getByText('create')).toBeTruthy()
    view.rerender(<McpServerPanel servers={servers} expandedId="drive" onToggle={onToggle} />)
    expect(screen.queryByText('create')).toBeNull()
    expect(screen.getByText('search')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Drive/ }).getAttribute('aria-expanded')).toBe('true')
  })
  it('handles a server with no tools without inventing tool names', () => {
    render(<McpServerPanel servers={servers} expandedId="mail" onToggle={vi.fn()} />)
    expect(screen.getByText('0 tools')).toBeTruthy()
    expect(screen.getByText('sse')).toBeTruthy()
    expect(screen.queryByText('list')).toBeNull()
  })
})

describe('ToolError retry and skip availability', () => {
  const base = { name: 'browser', target: 'https://app.example', message: 'Navigation failed',
    attempt: 1, maxAttempts: 4, retrying: false }
  it('never exposes an active retry without a caller action', () => {
    render(<ToolError {...base} />)
    expect((screen.getByRole('button', { name: 'Retry' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Skip' }) as HTMLButtonElement).disabled).toBe(true)
  })
  it('keeps skip available while a retry is pending when the caller allows it', () => {
    const onSkip = vi.fn()
    const onRetry = vi.fn()
    render(<ToolError {...base} retrying onSkip={onSkip} onRetry={onRetry} />)
    expect((screen.getByRole('button', { name: 'Retrying' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Skip' }) as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(onSkip).toHaveBeenCalledTimes(1)
    expect(onRetry).not.toHaveBeenCalled()
  })
  it('updates the attempt budget from parent state without calling retry again', () => {
    const onRetry = vi.fn()
    const view = render(<ToolError {...base} onRetry={onRetry} />)
    expect(screen.getByText('1/4')).toBeTruthy()
    view.rerender(<ToolError {...base} attempt={2} maxAttempts={5} onRetry={onRetry} />)
    expect(screen.getByText('2/5')).toBeTruthy()
    expect(onRetry).not.toHaveBeenCalled()
  })
  it('updates error text when a retry produces a new failure', () => {
    const view = render(<ToolError {...base} onRetry={vi.fn()} />)
    expect(screen.getByText('Navigation failed')).toBeTruthy()
    view.rerender(<ToolError {...base} message="Browser timed out" attempt={2} onRetry={vi.fn()} />)
    expect(screen.getByText('Browser timed out')).toBeTruthy()
    expect(screen.queryByText('Navigation failed')).toBeNull()
  })
  it('treats an error string containing markup as plain text', () => {
    const { container } = render(<ToolError {...base} message="<script>bad()</script>" />)
    expect(screen.getByText('<script>bad()</script>')).toBeTruthy()
    expect(container.querySelector('script')).toBeNull()
  })
})

describe('ToolError with real failure segments lacking retry telemetry', () => {
  const failure = { name: 'browser', target: 'https://app.example', message: 'Navigation failed' }

  it('keeps the actual operation, target and message without inventing an attempt count', () => {
    const { container } = render(<ToolError {...failure} />)
    expect(screen.getByText('browser')).toBeTruthy()
    expect(screen.getByText('https://app.example')).toBeTruthy()
    expect(screen.getByText('Navigation failed')).toBeTruthy()
    expect(container.querySelector('[data-slot="tool-error"]')).toBeTruthy()
    expect(container.querySelector('.tabular-nums')).toBeNull()
    expect((screen.getByRole('button', { name: 'Retry' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Skip' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('does not show a partial retry budget as if its missing value were known', () => {
    const view = render(<ToolError {...failure} attempt={2} />)
    expect(view.container.querySelector('.tabular-nums')).toBeNull()
    view.rerender(<ToolError {...failure} maxAttempts={4} />)
    expect(view.container.querySelector('.tabular-nums')).toBeNull()
    expect(screen.getByText('Navigation failed')).toBeTruthy()
  })

  it('shows exact retry data when a caller supplies both numbers later', () => {
    const view = render(<ToolError {...failure} />)
    expect(view.container.querySelector('.tabular-nums')).toBeNull()
    view.rerender(<ToolError {...failure} attempt={2} maxAttempts={4} />)
    expect(screen.getByText('2/4')).toBeTruthy()
    view.rerender(<ToolError {...failure} />)
    expect(screen.queryByText('2/4')).toBeNull()
  })

  it('uses only the caller retry action and state when telemetry is unavailable', () => {
    const onRetry = vi.fn()
    const onSkip = vi.fn()
    const view = render(<ToolError {...failure} onRetry={onRetry} onSkip={onSkip} />)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(onSkip).not.toHaveBeenCalled()
    view.rerender(<ToolError {...failure} retrying onRetry={onRetry} onSkip={onSkip} />)
    const retry = screen.getByRole('button', { name: 'Retrying' }) as HTMLButtonElement
    expect(retry.disabled).toBe(true)
    fireEvent.click(retry)
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(view.container.querySelector('.tabular-nums')).toBeNull()
  })
})

describe('PermissionGrant authority boundary through prop changes', () => {
  it('removes decision buttons when the caller reports an always grant', () => {
    const onGrant = vi.fn()
    const view = render(<PermissionGrant capability="Files" requester="Writer" reach={['Read']}
      scope="pending" onGrant={onGrant} />)
    expect(screen.getByRole('button', { name: 'Always' })).toBeTruthy()
    view.rerender(<PermissionGrant capability="Files" requester="Writer" reach={['Read']}
      scope="always" onGrant={onGrant} />)
    expect(screen.getByText('granted · always')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
  it('keeps final denial even when reach data changes afterward', () => {
    const view = render(<PermissionGrant capability="Files" requester="Writer" reach={['Read']}
      scope="denied" onGrant={vi.fn()} />)
    view.rerender(<PermissionGrant capability="Files" requester="Writer" reach={['Read', 'Write']}
      scope="denied" onGrant={vi.fn()} />)
    expect(screen.getByText('denied')).toBeTruthy()
    expect(screen.getByText('Write')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
  it('updates displayed reach without granting a new scope', () => {
    const onGrant = vi.fn()
    const view = render(<PermissionGrant capability="Files" requester="Writer" reach={['Read']}
      scope="pending" onGrant={onGrant} />)
    expect(screen.getByText('Read')).toBeTruthy()
    view.rerender(<PermissionGrant capability="Files" requester="Writer" reach={['Write']}
      scope="pending" onGrant={onGrant} />)
    expect(screen.getByText('Write')).toBeTruthy()
    expect(screen.queryByText('Read')).toBeNull()
    expect(onGrant).not.toHaveBeenCalled()
  })
  it('reports a grant only after the caller changes scope', () => {
    const onGrant = vi.fn()
    render(<PermissionGrant capability="Files" requester="Writer" reach={['Read']}
      scope="pending" onGrant={onGrant} />)
    fireEvent.click(screen.getByRole('button', { name: 'Always' }))
    expect(onGrant).toHaveBeenCalledExactlyOnceWith('always')
    expect(screen.queryByText('granted · always')).toBeNull()
  })
  it('does not invent a permission decision for an empty reach list', () => {
    render(<PermissionGrant capability="Files" requester="Writer" reach={[]}
      scope="pending" />)
    expect(screen.getByText('pending')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByText('granted · session')).toBeNull()
  })
})

describe('ComputerUse active step boundaries', () => {
  it('moves cursor and trail when the caller changes the active index', () => {
    const view = render(<ComputerUse url="https://app.example" steps={steps} activeIndex={0}>
      <div>Live frame</div>
    </ComputerUse>)
    const first = view.container.querySelector('svg[style*="left"]') as SVGElement
    expect(first.getAttribute('style')).toContain('left: 10%')
    view.rerender(<ComputerUse url="https://app.example" steps={steps} activeIndex={1}>
      <div>Live frame</div>
    </ComputerUse>)
    const second = view.container.querySelector('svg[style*="left"]') as SVGElement
    expect(second.getAttribute('style')).toContain('left: 45%')
    expect(screen.getByText('2/4')).toBeTruthy()
    expect(screen.queryByText('1/4')).toBeNull()
  })
  it('does not leave a stale cursor after steps become empty', () => {
    const view = render(<ComputerUse url="https://app.example" steps={steps} activeIndex={2}>
      <div>Live frame</div>
    </ComputerUse>)
    expect(view.container.querySelector('svg[style*="left"]')).toBeTruthy()
    view.rerender(<ComputerUse url="https://app.example" steps={[]} activeIndex={2}>
      <div>Live frame</div>
    </ComputerUse>)
    expect(view.container.querySelector('svg[style*="left"]')).toBeNull()
    expect(screen.queryByText('3/4')).toBeNull()
  })
  it('keeps a caller provided frame as the index changes', () => {
    const view = render(<ComputerUse url="https://app.example" steps={steps} activeIndex={0}>
      <img alt="Frame from agent" src="data:image/png;base64,aGVsbG8=" />
    </ComputerUse>)
    view.rerender(<ComputerUse url="https://app.example" steps={steps} activeIndex={3}>
      <img alt="Frame from agent" src="data:image/png;base64,aGVsbG8=" />
    </ComputerUse>)
    expect(screen.getByAltText('Frame from agent')).toBeTruthy()
    expect(screen.getByText('4/4')).toBeTruthy()
  })
  it('passes through caller metadata to the computer surface', () => {
    const view = render(<ComputerUse url="https://app.example" steps={[]} activeIndex={0}
      data-run-id="run-9" className="consumer-frame"><div>Frame</div></ComputerUse>)
    const card = view.container.querySelector('[data-slot="computer-use"]')!
    expect(card.getAttribute('data-run-id')).toBe('run-9')
    expect(card.className).toContain('consumer-frame')
  })
})

describe('CodeRunner state transitions and action availability', () => {
  const base = { language: 'python', code: 'print(1)', output: [] as string[] }
  it('disables the run affordance when no handler is supplied', () => {
    render(<CodeRunner {...base} state="idle" />)
    expect((screen.getByRole('button', { name: 'Run this snippet' }) as HTMLButtonElement).disabled).toBe(true)
  })
  it('does not fabricate output or duration in idle state', () => {
    render(<CodeRunner {...base} state="idle" onRun={vi.fn()} />)
    expect(screen.queryByText('output')).toBeNull()
    expect(screen.queryByText(/ms$/)).toBeNull()
    expect(screen.getByText('print(1)')).toBeTruthy()
  })
  it('updates output lines as a caller supplied run progresses', () => {
    const onRun = vi.fn()
    const view = render(<CodeRunner {...base} state="running" output={['boot']} onRun={onRun} />)
    expect(screen.getByText('boot')).toBeTruthy()
    view.rerender(<CodeRunner {...base} state="running" output={['boot', 'result']} onRun={onRun} />)
    expect(screen.getByText('boot')).toBeTruthy()
    expect(screen.getByText('result')).toBeTruthy()
    expect(onRun).not.toHaveBeenCalled()
  })
  it('restores the run action when execution completes', () => {
    const onRun = vi.fn()
    const view = render(<CodeRunner {...base} state="running" output={['boot']} onRun={onRun} />)
    expect((screen.getByRole('button', { name: 'Run this snippet' }) as HTMLButtonElement).disabled).toBe(true)
    view.rerender(<CodeRunner {...base} state="ok" output={['done']} durationMs={20} onRun={onRun} />)
    expect((screen.getByRole('button', { name: 'Run this snippet' }) as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText('20ms')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Run this snippet' }))
    expect(onRun).toHaveBeenCalledTimes(1)
  })
  it('shows a failed run distinctly after a running state', () => {
    const view = render(<CodeRunner {...base} state="running" output={['boot']} onRun={vi.fn()} />)
    view.rerender(<CodeRunner {...base} state="error" output={['Permission denied']}
      durationMs={100} onRun={vi.fn()} />)
    expect(screen.queryByText('boot')).toBeNull()
    expect(screen.getByText('Permission denied').className).toContain('text-red-600')
    expect(screen.getByText('100ms')).toBeTruthy()
  })
  it('does not execute caller code merely by rendering a snippet', () => {
    const onRun = vi.fn()
    render(<CodeRunner language="shell" code="rm -rf /tmp/example" state="idle"
      output={[]} onRun={onRun} />)
    expect(screen.getByText('rm -rf /tmp/example')).toBeTruthy()
    expect(onRun).not.toHaveBeenCalled()
  })
})
