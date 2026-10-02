import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { GenUiWidget } from './GenUiWidget'
import { GenUiHostCtx, type GenUiHost } from './actions'
import { parseGenUiEnvelope, type GenUiElementState, type GenUiV2Element } from './envelope'
import { getGenUiPersistenceKey } from './widgetState'
import { WIDGET_ACTION_EVENT } from '../widget/actionTurn'

const host: GenUiHost = { producer: { kind: 'chat' }, scopeId: 'owner-7', conversationId: 'chat-9' }

function content(revision: number, elements: Record<string, GenUiV2Element>, state: Record<string, GenUiElementState> = {}, root = 'root') {
  return JSON.stringify({ schemaVersion: 2, id: 'decision-panel', revision, root, elements, state })
}

function interactive(revision: number, title: string, includeDraft = true) {
  return content(revision, {
    root: { type: 'Stack', props: { gap: 'm' }, children: includeDraft ? ['choice', 'draft'] : ['choice'] },
    choice: { type: 'Compare', props: { title, items: [
      { id: 'alpha', label: 'Alpha', description: 'First option' },
      { id: 'beta', label: 'Beta', description: 'Second option' },
    ] } },
    ...(includeDraft ? { draft: { type: 'Form', props: { title: 'Details', fields: ['amount'], action: 'save' } } } : {}),
  })
}

function actionable(revision: number, label: string, action: string) {
  return content(revision, { root: { type: 'Button', props: { label, action } } })
}

function renderWidget(source: string, activeHost: GenUiHost = host, streaming = false) {
  return render(<GenUiHostCtx.Provider value={activeHost}>
    <GenUiWidget content={source} title="Decision" streaming={streaming} />
  </GenUiHostCtx.Provider>)
}

beforeEach(() => localStorage.clear())
afterEach(cleanup)

describe('GenUI v2 envelope validation', () => {
  it('accepts the catalog contract and rejects malformed props, graphs, duplicate IDs, and unsafe URLs', () => {
    expect(parseGenUiEnvelope(interactive(1, 'Choose')).kind).toBe('v2')

    const invalidCases = [
      content(1, { root: { type: 'Callout', props: { text: 'ok', extra: true } } }),
      content(1, { root: { type: 'ProgressBar', props: { value: 'many' } } }),
      content(1, { root: { type: 'Stack', props: {}, children: ['missing'] } }),
      content(1, {
        root: { type: 'Stack', props: {}, children: ['child'] },
        child: { type: 'Card', props: {}, children: ['root'] },
      }),
      content(1, {
        root: { type: 'Callout', props: { text: 'visible' } },
        orphan: { type: 'Badge', props: { text: 'hidden' } },
      }),
      content(1, { root: { type: 'Sources', props: { items: [{ id: 'bad', label: 'Bad', url: 'javascript:alert(1)' }] } } }),
      '{"schemaVersion":2,"id":"dupe","revision":1,"root":"root","elements":{"root":{"type":"Callout","props":{"text":"one"}},"root":{"type":"Callout","props":{"text":"two"}}},"state":{}}',
    ]
    for (const source of invalidCases) expect(parseGenUiEnvelope(source).kind, source).toBe('invalid')
  })

  it('distinguishes partial streamed JSON from invalid completed input', () => {
    expect(parseGenUiEnvelope('{"schemaVersion":2').kind).toBe('incomplete')
    expect(parseGenUiEnvelope('{"schemaVersion":3}').kind).toBe('invalid')
    expect(parseGenUiEnvelope('root = Callout(text: "legacy")').kind).toBe('legacy')
  })
})

describe('GenUI v2 state continuity', () => {
  it('retains compatible selection, filter, and drafts across revisions and reload', async () => {
    const view = renderWidget(interactive(1, 'Choose'))
    fireEvent.change(screen.getByLabelText('Filter comparison options'), { target: { value: 'alp' } })
    fireEvent.click(screen.getByRole('radio', { name: /Alpha/ }))
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '42' } })

    view.rerender(<GenUiHostCtx.Provider value={host}>
      <GenUiWidget content={interactive(2, 'Newest')} title="Decision" />
    </GenUiHostCtx.Provider>)
    await screen.findByText('Newest')
    expect(screen.getByLabelText('Filter comparison options')).toHaveValue('alp')
    expect(screen.getByRole('radio', { name: /Alpha/ })).toBeChecked()
    expect(screen.getByLabelText('Amount')).toHaveValue('42')

    view.unmount()
    renderWidget(interactive(2, 'Reloaded'))
    expect(screen.getByLabelText('Filter comparison options')).toHaveValue('alp')
    expect(screen.getByRole('radio', { name: /Alpha/ })).toBeChecked()
    expect(screen.getByLabelText('Amount')).toHaveValue('42')
  })

  it('isolates conversations and clears removed element state before an ID returns', async () => {
    const view = renderWidget(interactive(1, 'Choose'))
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: 'private draft' } })
    view.rerender(<GenUiHostCtx.Provider value={host}>
      <GenUiWidget content={interactive(2, 'Without draft', false)} title="Decision" />
    </GenUiHostCtx.Provider>)
    await waitFor(() => expect(screen.queryByLabelText('Amount')).toBeNull())
    view.rerender(<GenUiHostCtx.Provider value={host}>
      <GenUiWidget content={interactive(3, 'Draft returns')} title="Decision" />
    </GenUiHostCtx.Provider>)
    await waitFor(() => expect(screen.getByLabelText('Amount')).toHaveValue(''))

    view.unmount()
    renderWidget(interactive(3, 'Other chat'), { ...host, conversationId: 'chat-other' })
    expect(screen.getByLabelText('Filter comparison options')).toHaveValue('')
    expect(screen.getByLabelText('Amount')).toHaveValue('')
  })

  it('rejects a lower revision while retaining the newest accepted tree', async () => {
    const view = renderWidget(interactive(2, 'Newest'))
    await screen.findByText('Newest')
    view.rerender(<GenUiHostCtx.Provider value={host}>
      <GenUiWidget content={interactive(1, 'Stale')} title="Decision" />
    </GenUiHostCtx.Provider>)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Rejected stale GenUI revision 1'))
    expect(screen.getByText('Newest')).toBeInTheDocument()
    expect(screen.queryByText('Stale')).toBeNull()
  })

  it('keeps a persisted revision fence across unmount and reload', async () => {
    const newest = renderWidget(interactive(2, 'Persisted newest'))
    await screen.findByText('Persisted newest')
    newest.unmount()

    renderWidget(interactive(1, 'Reloaded stale'))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Rejected stale GenUI revision 1'))
    expect(screen.queryByText('Reloaded stale')).toBeNull()
  })

  it('bounds restored text and persists only current elements and declared fields', async () => {
    const key = getGenUiPersistenceKey(host, 'decision-panel')!
    localStorage.setItem(key, JSON.stringify({
      revision: 1,
      types: { root: 'Stack', choice: 'Compare', draft: 'Form', ghost: 'Form' },
      state: {
        choice: { filter: 'x'.repeat(5000) },
        draft: { fields: { amount: '9'.repeat(5000), removed: 'discard me' } },
        ghost: { fields: { amount: 'discard me' } },
      },
    }))
    renderWidget(interactive(1, 'Bounded'))
    expect((screen.getByLabelText('Filter comparison options') as HTMLInputElement).value).toHaveLength(4096)
    expect((screen.getByLabelText('Amount') as HTMLInputElement).value).toHaveLength(4096)
    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem(key)!) as { state: Record<string, { fields?: Record<string, string> }> }
      expect(stored.state.ghost).toBeUndefined()
      expect(stored.state.draft.fields).toEqual({ amount: '9'.repeat(4096) })
    })
  })

  it('fences an older concurrently mounted widget before it can act or overwrite storage', async () => {
    const published: string[] = []
    const onPublished = (event: Event) => published.push(String((event as CustomEvent).detail?.text ?? ''))
    window.addEventListener(WIDGET_ACTION_EVENT, onPublished)
    try {
      renderWidget(actionable(1, 'Run old', 'old_action'))
      const oldControl = screen.getByRole('button', { name: 'Run old' })
      renderWidget(actionable(2, 'Run current', 'current_action'))
      const key = getGenUiPersistenceKey(host, 'decision-panel')!

      await waitFor(() => {
        expect(screen.queryByRole('button', { name: 'Run old' })).toBeNull()
        expect(JSON.parse(localStorage.getItem(key)!).revision).toBe(2)
      })
      fireEvent.click(oldControl)
      expect(published).toEqual([])
      fireEvent.click(screen.getByRole('button', { name: 'Run current' }))
      await waitFor(() => expect(published).toEqual(['[UI] current_action']))
      expect(JSON.parse(localStorage.getItem(key)!).revision).toBe(2)
    } finally { window.removeEventListener(WIDGET_ACTION_EVENT, onPublished) }
  })

  it('persists only with verified host identities and never places a workflow token in the key', () => {
    expect(getGenUiPersistenceKey({ producer: { kind: 'chat' }, conversationId: 'chat' }, 'panel')).toBeNull()
    const key = getGenUiPersistenceKey({
      producer: { kind: 'workflow-gate', runId: 'run-1', token: 'secret-resume-token' },
      scopeId: 'owner', conversationId: 'run-1:node-2',
    }, 'panel')!
    expect(key).toContain('workflow-gate')
    expect(key).not.toContain('secret-resume-token')
  })
})

describe('GenUI rendering compatibility', () => {
  it('shows bounded v2 diagnostics, treats streamed partial JSON as loading, and keeps legacy DSL available', () => {
    const invalid = renderWidget(content(1, { root: { type: 'Callout', props: { text: 7 } } }))
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid GenUI v2')
    invalid.unmount()

    const partial = renderWidget('{"schemaVersion":2', host, true)
    expect(screen.getByRole('status')).toHaveTextContent('Loading interface')
    partial.unmount()

    renderWidget('root = Callout(text: "Legacy remains")')
    expect(screen.getByText('Legacy remains')).toBeInTheDocument()
  })
})
