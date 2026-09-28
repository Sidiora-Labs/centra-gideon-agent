import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { beforeEach, expect, it } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { resetDataStore, writeQuery } from '../../shared/data/data'
import type { ActionProvider, WorkflowDef } from '../../shared/data/api'
import { ActionConfig, coerceActionConfig } from './ActionConfig'
import { WorkflowInputFields } from '../workflows/workflowWidgets'

const definition: WorkflowDef = {
  name: 'Daily summary', root: { kind: 'sequence', id: 'root', children: [] },
  inputs: {
    project: { type: 'string', required: true, description: 'Project to summarize' },
    limit: { type: 'integer', default: 0 },
    enabled: { type: 'boolean', default: false },
    options: { type: 'object', default: { compact: true } },
  },
}
const provider: ActionProvider = {
  name: 'run-workflow', display_name: 'Run workflow', supports_blocking: false,
  settingsSchema: { type: 'object', required: ['workflow'], properties: {
    workflow: { type: 'string', 'x-meta': { widget: 'workflow', label: 'Workflow' } },
    inputs: { type: 'object', 'x-meta': { widget: 'workflow-inputs', label: 'Inputs' } },
  } },
}
function Form() {
  const [config, setConfig] = useState<Record<string, unknown>>({ workflow: definition.name, inputs: {} })
  return <><ActionConfig providers={[provider]} provider="run-workflow" config={config} onProvider={() => undefined} onConfig={setConfig} vars={[]} />
    <output aria-label="Saved action">{JSON.stringify(coerceActionConfig([provider], provider.name, config).config)}</output></>
}
beforeEach(() => resetDataStore())
it('renders the real workflow action widgets and preserves declared typed defaults in saved configuration', () => {
  writeQuery('workflow-action:definitions', [{ name: definition.name, description: 'Summarize a project', source: 'user', version: 1, tags: [], provider: 'native' }])
  writeQuery(`workflow-action:definition:${definition.name}`, definition)
  const ui = render(<Form />)
  expect(ui.getByText('Project to summarize')).toBeInTheDocument()
  fireEvent.change(ui.getByLabelText('project'), { target: { value: 'Acme' } })
  fireEvent.change(ui.getByLabelText('options'), { target: { value: '{"compact":false}' } })
  const saved = JSON.parse(ui.getByLabelText('Saved action').textContent || '{}')
  expect(saved.workflow).toBe(definition.name)
  expect(saved.inputs).toEqual({ project: 'Acme', limit: 0, enabled: false, options: { compact: false } })
  expect(ui.getByRole('spinbutton')).toHaveValue(0)
})
it('keeps malformed existing inputs visible instead of replacing them with defaults', () => {
  function Invalid() {
    const [value, setValue] = useState<unknown>('{bad')
    return <WorkflowInputFields definition={definition} value={value} onChange={setValue} />
  }
  const ui = render(<Invalid />)
  expect(ui.getByRole('alert')).toHaveTextContent('Inputs must be a JSON object')
  expect(ui.getByLabelText('Workflow inputs JSON')).toHaveValue('{bad')
  fireEvent.change(ui.getByLabelText('Workflow inputs JSON'), { target: { value: '{"project":"Acme"}' } })
  expect(ui.getByLabelText('project')).toHaveValue('Acme')
  expect(ui.getByRole('spinbutton')).toHaveValue(0)
})
