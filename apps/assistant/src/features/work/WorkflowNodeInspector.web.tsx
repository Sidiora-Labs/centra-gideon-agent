import React from 'react'

export type WorkflowEditableNode = Readonly<{
  id: string
  kind: string
  label: string
  prompt: string
  needs: readonly string[]
}>

export type WorkflowNodeChoice = Readonly<{ id: string; label: string }>

export default function WorkflowNodeInspector(props: Readonly<{
  node: WorkflowEditableNode
  choices: readonly WorkflowNodeChoice[]
  onChange: (change: Partial<Pick<WorkflowEditableNode, 'label' | 'prompt'>>) => void
  onToggleNeed: (id: string) => void
}>) {
  return <section aria-label="Workflow node inspector" style={{ display: 'grid', gap: 10, minWidth: 0 }}>
    <h3 style={{ margin: 0 }}>Node inspector</h3>
    <p style={{ margin: 0 }}>Selected {props.node.kind} node · {props.node.id}</p>
    <label style={{ display: 'grid', gap: 5 }}>
      Node label
      <input aria-label="Node label" value={props.node.label}
        onChange={event => props.onChange({ label: event.target.value })} />
    </label>
    <label style={{ display: 'grid', gap: 5 }}>
      Instructions
      <textarea aria-label="Node instructions" rows={5} value={props.node.prompt}
        onChange={event => props.onChange({ prompt: event.target.value })} />
    </label>
    <fieldset style={{ border: '1px solid color-mix(in srgb, currentColor 20%, transparent)', borderRadius: 10,
      padding: 10, display: 'grid', gap: 6 }}>
      <legend>Input ports · run after</legend>
      {props.choices.length === 0 && <span>No other named nodes can connect yet.</span>}
      {props.choices.map(choice => <label key={choice.id} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input type="checkbox" checked={props.node.needs.includes(choice.id)}
          onChange={() => props.onToggleNeed(choice.id)} />
        {choice.label}
      </label>)}
    </fieldset>
  </section>
}
