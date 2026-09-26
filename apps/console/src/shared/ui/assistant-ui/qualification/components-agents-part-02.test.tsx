import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { Artifact, JudgeBenchRecommendation, SavedAgent, TaskItem, WorkflowContinuation, WorkflowRunDetailData } from '../../../data/api'
import { ArtifactResult, JudgeRecommendationResult, QUESTION_FLOW_PROVENANCE, RunHandoffResult, WorkflowQuestionFlow } from '../../../../features/chat/auiAgentResults'
import { GideonAgentCard } from '../../../../features/agents/auiAgentPanel'
import { TaskTodoResult } from '../../../../features/tasks/auiTaskCards'
import { AgentCard } from '../../../vendor/assistant-ui/elements/agent-card'
import { RecommendationCard } from '../../../vendor/assistant-ui/elements/recommendation-card'
import { AgentHandoff } from '../../../vendor/assistant-ui/elements/agent-handoff'

const continuation: WorkflowContinuation = {
  resume_token: 'token-27', node_id: 'choose-route', instance_path: 'choose-route',
  ask: { kind: 'choice', prompt: 'Which route?', choices: ['north', 'south'] },
  handoff: {}, expires_at: 1999999999, expired: false,
}

const recommendation: JudgeBenchRecommendation = {
  rubric_class: 'quality', verdict: 'Use the smaller model', tier: 'candidate',
  samples: 12, use_case: 'Incident summaries', model_ref: 'model-17',
  cost_usd: null, notes: ['Matched the recorded criteria.'],
}

describe('part 02: real workflow question composition', () => {
  it('attributes the new composition to donor primitives', () => {
    expect(QUESTION_FLOW_PROVENANCE).toContain('Gideon composition')
    expect(QUESTION_FLOW_PROVENANCE).toContain('task-card.tsx')
  })

  it('renders only choices carried by the workflow continuation', () => {
    render(<WorkflowQuestionFlow runId="run-42" continuation={continuation} onResolved={() => {}} />)
    expect(screen.getByLabelText('Question for run run-42')).toHaveTextContent('Which route?')
    expect(screen.getByRole('button', { name: 'north' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'south' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'east' })).toBeNull()
  })

  it('shows expiry and removes all response controls', () => {
    render(<WorkflowQuestionFlow runId="run-42" continuation={{ ...continuation, expired: true }} onResolved={() => {}} />)
    expect(screen.getByRole('status')).toHaveTextContent('Question expired for run run-42')
    expect(screen.queryByRole('button', { name: 'north' })).toBeNull()
  })

  it('does not invent a prompt when the producer omitted it', () => {
    render(<WorkflowQuestionFlow runId="run-42" continuation={{ ...continuation, ask: { kind: 'choice' } }} onResolved={() => {}} />)
    expect(screen.getByRole('status')).toHaveTextContent('Question unavailable for run run-42')
  })

  it('leaves approval and structured forms with their existing authorized workflow handler', () => {
    const { rerender } = render(<WorkflowQuestionFlow runId="run-42"
      continuation={{ ...continuation, ask: { kind: 'approval', prompt: 'Allow action?' } }} onResolved={() => {}} />)
    expect(screen.getByRole('status')).toHaveTextContent('answered in the run inspector')
    expect(screen.queryByRole('button', { name: 'north' })).toBeNull()
    rerender(<WorkflowQuestionFlow runId="run-42"
      continuation={{ ...continuation, ask: { kind: 'form', prompt: 'Enter fields', fields: [{ name: 'target', type: 'string' }] } }} onResolved={() => {}} />)
    expect(screen.getByRole('status')).toHaveTextContent('answered in the run inspector')
  })

  it('renders a required freeform answer instead of a fabricated choice', () => {
    render(<WorkflowQuestionFlow runId="run-42" continuation={{ ...continuation, ask: { kind: 'text', prompt: 'What changed?' } }} onResolved={() => {}} />)
    expect(screen.getByText('What changed?')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Answer' })).toBeRequired()
    expect(screen.getByRole('button', { name: 'Send answer' })).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: 'Answer' }), { target: { value: 'A file changed' } })
    expect(screen.getByRole('button', { name: 'Send answer' })).toBeEnabled()
  })

  it('does not turn an empty choice request into a text answer', () => {
    render(<WorkflowQuestionFlow runId="run-42"
      continuation={{ ...continuation, ask: { kind: 'choice', prompt: 'Which route?', choices: [] } }} onResolved={() => {}} />)
    expect(screen.getByRole('status')).toHaveTextContent('No choices were supplied for run run-42')
    expect(screen.queryByRole('textbox')).toBeNull()
  })
})

describe('part 02: recommendation, artifact, and handoff evidence', () => {
  it('shows the real recommendation without invented confidence or actions', () => {
    const { container } = render(<JudgeRecommendationResult recommendation={recommendation} />)
    expect(container.querySelector('[data-slot="recommendation-card"]')).toBeInTheDocument()
    expect(screen.getByText('Use the smaller model')).toBeInTheDocument()
    expect(screen.getByText('Matched the recorded criteria.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Accept' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Alternatives' })).toBeNull()
    expect(container.textContent).not.toMatch(/confidence/i)
  })

  it('uses the use case only when recorded notes are absent', () => {
    render(<JudgeRecommendationResult recommendation={{ ...recommendation, notes: [] }} />)
    expect(screen.getByText('Incident summaries')).toBeInTheDocument()
  })

  it('opens the actual artifact slug through the supplied navigation action', () => {
    const artifact: Artifact = {
      slug: 'artifact-71', name: 'Incident report', kind: 'document', source: 'manual',
      description: '', tags: [], version: 4, created_at: '', updated_at: '',
      events: [], source_path: '', readonly: false,
    }
    let opened = ''
    render(<ArtifactResult artifact={artifact} onOpen={slug => { opened = slug }} />)
    expect(screen.getByText('document · version 4')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open artifact Incident report' }))
    expect(opened).toBe('artifact-71')
  })

  it('renders only handoffs with both real roles and a continuing next step', () => {
    const run = {
      run_id: 'run-42', workflow: 'review', status: 'running', spec_version: 1,
      nodes: [], round_handoff: {
        first: { completed_role: 'Builder', next_role: 'Reviewer', next_allowed_paths: ['src/a.ts'] },
        stopped: { completed_role: 'Reviewer', next_role: 'Publisher', stop: true },
        missing: { completed_role: 'Builder' },
      },
    } as WorkflowRunDetailData
    const { container } = render(<RunHandoffResult run={run} />)
    expect(container.querySelectorAll('[data-slot="agent-handoff"]')).toHaveLength(1)
    expect(screen.getByLabelText('Handoffs for run run-42')).toHaveTextContent('Builder')
    expect(screen.getByLabelText('Handoffs for run run-42')).toHaveTextContent('Reviewer')
    expect(screen.getByText('src/a.ts')).toBeInTheDocument()
    expect(screen.queryByText('Publisher')).toBeNull()
  })
})

describe('part 02: saved agent and task plan records', () => {
  it('does not show a connect action or version that SavedAgent cannot establish', () => {
    const agent: SavedAgent = { name: 'Analyst', provider: 'native', skills: ['Research'] }
    const { container } = render(<GideonAgentCard agent={agent} />)
    expect(container.querySelector('[data-slot="agent-card"]')).toBeInTheDocument()
    expect(screen.getByText('Analyst')).toBeInTheDocument()
    expect(screen.getByText('Research')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull()
    expect(screen.queryByText(/^vundefined$/)).toBeNull()
  })

  it('shows stored task steps without treating pending work as active', () => {
    const task: TaskItem = { id: 'task-7', title: 'Build page', status: 'open', action_plan: [
      { sequence: 2, content: 'Inspect layout', completed: true },
      { sequence: 3, description: 'Check route', completed: false },
    ] }
    const { container } = render(<TaskTodoResult task={task} />)
    expect(container.querySelector('[data-slot="todo-list"]')).toBeInTheDocument()
    expect(screen.getByText('Inspect layout')).toBeInTheDocument()
    expect(screen.getByText('Check route')).toBeInTheDocument()
    expect(screen.getByText('1/2')).toBeInTheDocument()
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(0)
  })

  it('does not create a plan from missing or blank action steps', () => {
    const task: TaskItem = { id: 'task-7', title: 'Build page', status: 'open' }
    const { container, rerender } = render(<TaskTodoResult task={task} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<TaskTodoResult task={{ ...task, action_plan: [{ completed: false }] }} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('part 02: source-derived donor optional controls', () => {
  it('renders recorded agent metadata and an authorized connect action when supplied', () => {
    let connected = false
    render(<AgentCard name="Remote analyst" provider="acp" description="Answers research tasks"
      version="2" endpoint="https://agent.example" model="model-x" skills={[{ name: 'Search', description: 'Web research' }]}
      onConnect={() => { connected = true }} />)
    expect(screen.getByText('v2')).toBeInTheDocument()
    expect(screen.getByText('https://agent.example')).toBeInTheDocument()
    expect(screen.getByText('model-x')).toBeInTheDocument()
    expect(screen.getByText('Web research')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(connected).toBe(true)
  })

  it('shows a connected status without an active connect action', () => {
    render(<AgentCard name="Connected analyst" provider="acp" description="" skills={[]} connected />)
    expect(screen.getByRole('button', { name: 'Connected' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull()
  })

  it('renders supplied confidence and only supplied recommendation actions', () => {
    let accepted = false
    let alternatives = false
    render(<RecommendationCard state="idle" question="Use model X?" confidenceLabel="72% recorded"
      acceptedLabel="Accepted" onAccept={() => { accepted = true }} onAlternatives={() => { alternatives = true }}>
      Bench evidence from run 42
    </RecommendationCard>)
    expect(screen.getByText('72% recorded')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Alternatives' }))
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
    expect(alternatives).toBe(true)
    expect(accepted).toBe(true)
  })

  it('renders an accepted recommendation without exposing idle actions', () => {
    render(<RecommendationCard state="accepted" question="Use model X?" acceptedLabel="Recorded as accepted">
      Bench evidence
    </RecommendationCard>)
    expect(screen.getByText('Recorded as accepted')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Accept' })).toBeNull()
  })

  it('shows a recorded handoff reason only when supplied', () => {
    const { rerender } = render(<AgentHandoff from="Builder" to="Reviewer" reason="Needs review"
      carried={['src/a.ts']} settled={false} />)
    expect(screen.getByText('Needs review')).toBeInTheDocument()
    rerender(<AgentHandoff from="Builder" to="Reviewer" carried={[]} settled />)
    expect(screen.queryByText('Needs review')).toBeNull()
    expect(screen.getByText('Reviewer')).toBeInTheDocument()
  })
})
