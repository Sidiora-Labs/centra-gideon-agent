import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ChatSession, PendingApproval, PlanStep, SavedAgent, SpawnedAgent, TaskItem } from '../../../data/api'
import { AgentOptionList, AgentPlanResult, OPTION_LIST_PROVENANCE, PendingApprovalResult, SessionAgentStatus, SubagentResults } from '../../../../features/chat/auiAgentResults'
import { GideonAgentStatus } from '../../../../features/agents/auiAgentPanel'
import { GideonTaskCard, taskCardState } from '../../../../features/tasks/auiTaskCards'

const plan: PlanStep[] = [
  { id: 'inspect', kind: 'work', title: 'Inspect sources', status: 'approved' },
  { id: 'change', kind: 'work', title: 'Change code', status: 'running' },
  { id: 'verify', kind: 'work', title: 'Verify result', status: 'pending' },
]
const session: ChatSession = {
  key: 'session-42', title: 'Launch', agent: 'Gideon', model: '', reasoning_effort: '',
  acp_provider: '', acp_provider_agent: '', mode: '', workspace_dir: '', messages: 2,
  running: true, stopping: false, pending_approval: false,
}

describe('part 01: truthful agent plan', () => {
  it('uses donor plan for a linear active plan and keeps actual step names', () => {
    const { container } = render(<AgentPlanResult steps={plan} />)
    expect(container.querySelector('[data-slot="agent-plan"]')).toBeInTheDocument()
    expect(screen.getByText('1 of 3')).toBeInTheDocument()
    expect(screen.getByText('Inspect sources')).toBeInTheDocument()
    expect(screen.getByText('Change code')).toBeInTheDocument()
    expect(screen.getByText('Verify result')).toBeInTheDocument()
  })

  it('does not invent progress when a reviewed step follows a pending step', () => {
    const steps = [plan[2], plan[0], plan[1]]
    const { container } = render(<AgentPlanResult steps={steps} />)
    expect(container.querySelector('[data-slot="agent-plan"]')).toBeNull()
    expect(screen.getByRole('list', { name: 'Agent plan' })).toHaveTextContent('Verify result — pending')
    expect(screen.getByRole('list', { name: 'Agent plan' })).toHaveTextContent('Inspect sources — approved')
  })

  it('does not animate an idle pending step as active work', () => {
    const { container } = render(<AgentPlanResult steps={[plan[0], plan[2]]} />)
    expect(container.querySelector('[data-slot="agent-plan"]')).toBeNull()
    expect(screen.getByText('Verify result — pending')).toBeInTheDocument()
  })

  it('renders no plan when the API returned no steps', () => {
    expect(render(<AgentPlanResult steps={[]} />).container).toBeEmptyDOMElement()
  })
})

describe('part 01: subagent and session identity', () => {
  const agents: SpawnedAgent[] = [
    { id: 'run-complete', task: 'Read documentation', done: true, agent: 'Scout' },
    { id: 'run-active', task: 'Inspect API', done: false, agent: 'Builder' },
    { id: 'run-failed', task: 'Run verification', done: true, error: 'Cannot reach runner' },
  ]

  it('shows only completed runs as completed in the donor list', () => {
    const { container } = render(<SubagentResults agents={agents} />)
    const list = container.querySelector('[data-slot="subagent-list"]')
    expect(list).toBeInTheDocument()
    expect(list).toHaveTextContent('Scout')
    expect(list).not.toHaveTextContent('Builder')
    expect(list).not.toHaveTextContent('run-failed')
    expect(screen.getByRole('progressbar', { name: 'Scout · run-complete progress' })).toHaveAttribute('aria-valuenow', '100')
  })

  it('keeps active and failed run IDs and error visible', () => {
    const { container } = render(<SubagentResults agents={agents} />)
    expect(container.querySelector('[data-state="working"]')).toHaveTextContent('Builder')
    expect(container.querySelector('[data-state="failed"]')).toHaveTextContent('run-failed')
    expect(screen.getByText('Cannot reach runner')).toBeInTheDocument()
    expect(screen.getByText('Inspect API')).toBeInTheDocument()
  })

  it('does not show a completed list with no completed runs', () => {
    const { container } = render(<SubagentResults agents={agents.slice(1)} />)
    expect(container.querySelector('[data-slot="subagent-list"]')).toBeNull()
  })

  it('binds session agent and session ID without an invented status', () => {
    render(<SessionAgentStatus session={session} />)
    expect(screen.getByLabelText('Agent Gideon, session session-42')).toHaveTextContent('Gideon: running')
  })

  it('states when the real session requests approval', () => {
    render(<SessionAgentStatus session={{ ...session, pending_approval: true }} />)
    expect(screen.getByText('Gideon: approval needed')).toBeInTheDocument()
  })

  it('uses the idle label after the session stops', () => {
    render(<SessionAgentStatus session={{ ...session, running: false }} />)
    expect(screen.getByText('Gideon: idle')).toBeInTheDocument()
  })
})

describe('part 01: saved agents and tasks', () => {
  const agent: SavedAgent = { name: 'Analyst', provider: 'native', running_sessions: 2 }
  const task: TaskItem = { id: 'task-91', title: 'Inspect incident', status: 'in_progress', description: 'Compare traces' }

  it('uses actual running session count and forwards the agent name to navigation', () => {
    let opened = ''
    render(<GideonAgentStatus agent={agent} onOpen={name => { opened = name }} />)
    expect(screen.getByText('Analyst: 2 running')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open agent' }))
    expect(opened).toBe('Analyst')
  })

  it('does not offer navigation without a handler', () => {
    render(<GideonAgentStatus agent={{ ...agent, running_sessions: 0 }} />)
    expect(screen.getByText('Analyst: idle')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open agent' })).toBeNull()
  })

  it('does not claim an idle agent when activity was omitted by the API', () => {
    render(<GideonAgentStatus agent={{ name: 'Analyst', provider: 'native' }} />)
    expect(screen.getByText('Analyst: activity unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Analyst: idle')).toBeNull()
  })

  it('maps persisted task statuses to donor status states', () => {
    expect(taskCardState('done')).toBe('done')
    expect(taskCardState('completed')).toBe('done')
    expect(taskCardState('active')).toBe('working')
    expect(taskCardState('in_progress')).toBe('working')
    expect(taskCardState('failed')).toBe('failed')
    expect(taskCardState('cancelled')).toBe('cancelled')
    expect(taskCardState('blocked')).toBe('waiting')
  })

  it('retains task ID, title, description, and real navigation callback', () => {
    let opened = ''
    render(<GideonTaskCard task={task} onSaved={() => {}} onOpen={id => { opened = id }} />)
    expect(screen.getByLabelText('Task task-91')).toHaveTextContent('Inspect incident')
    expect(screen.getByText('Compare traces')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open task' }))
    expect(opened).toBe('task-91')
  })

  it('does not offer completion for a locked project task or an already completed task', () => {
    const { rerender } = render(<GideonTaskCard task={{ ...task, provider: 'project' }} onSaved={() => {}} />)
    expect(screen.queryByRole('button', { name: 'Complete task' })).toBeNull()
    rerender(<GideonTaskCard task={{ ...task, status: 'done' }} onSaved={() => {}} />)
    expect(screen.queryByRole('button', { name: 'Complete task' })).toBeNull()
  })
})

describe('part 01: Gideon option list composition', () => {
  it('records its provenance without claiming a donor option-list element', () => {
    expect(OPTION_LIST_PROVENANCE).toContain('Gideon composition')
    expect(OPTION_LIST_PROVENANCE).toContain('task-card.tsx')
  })

  it('passes the selected stable ID to a real async callback and records selection', async () => {
    const selected: string[] = []
    render(<AgentOptionList title="Choose a route" options={[
      { id: 'north', label: 'North', description: 'Northern route' },
      { id: 'south', label: 'South' },
    ]} onSelect={async id => { selected.push(id); return { ok: true } }} />)
    fireEvent.click(screen.getByRole('button', { name: 'North' }))
    await waitFor(() => expect(selected).toEqual(['north']))
    expect(await screen.findByText('North — selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'South' })).toBeDisabled()
  })

  it('shows a rejected action and allows a subsequent selection attempt', async () => {
    const selected: string[] = []
    render(<AgentOptionList title="Choose a route" options={[{ id: 'north', label: 'North' }]}
      onSelect={async id => { selected.push(id); if (selected.length === 1) throw new Error('Action refused'); return { ok: true } }} />)
    fireEvent.click(screen.getByRole('button', { name: 'North' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Action refused')
    expect(screen.getByRole('button', { name: 'North' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'North' }))
    await waitFor(() => expect(selected).toEqual(['north', 'north']))
    expect(await screen.findByText('North — selected')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('does not mark an unconfirmed server response as a selected option', async () => {
    render(<AgentOptionList title="Choose a route" options={[{ id: 'north', label: 'North' }]}
      onSelect={async () => ({ ok: false })} />)
    fireEvent.click(screen.getByRole('button', { name: 'North' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Selection was not confirmed')
    expect(screen.getByRole('button', { name: 'North' })).toBeEnabled()
    expect(screen.queryByText('North — selected')).toBeNull()
  })

  it('states when no selectable options came from the producer', () => {
    render(<AgentOptionList title="Choose a route" options={[]} onSelect={async () => ({ ok: true })} />)
    expect(screen.getByText('No options available')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'North' })).toBeNull()
  })
})

describe('part 01: pending approval identity', () => {
  const approval: PendingApproval = {
    id: 'approval-14', source: 'runner', tool: 'shell',
    tool_input: { command: 'git status' }, tool_purpose: 'Inspect current changes',
    session: 'session-42', ts: 1720000000,
  }

  it('renders the actual pending request and only decisions supported by the API', () => {
    render(<PendingApprovalResult approval={approval} onResolved={() => {}} />)
    expect(screen.getByLabelText('Approval approval-14')).toHaveTextContent('shell')
    expect(screen.getByText('Inspect current changes')).toBeInTheDocument()
    expect(screen.getByText('{"command":"git status"}')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Allow once' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Always allow' })).toBeNull()
    expect(screen.queryByText('Finished with exit 0')).toBeNull()
  })

  it('shows a literal tool input without adding a fabricated command', () => {
    render(<PendingApprovalResult approval={{ ...approval, tool_input: 'inspect --dry-run', tool_purpose: undefined }}
      onResolved={() => {}} />)
    expect(screen.getByText('inspect --dry-run')).toBeInTheDocument()
    expect(screen.getByText('runner')).toBeInTheDocument()
  })
})
