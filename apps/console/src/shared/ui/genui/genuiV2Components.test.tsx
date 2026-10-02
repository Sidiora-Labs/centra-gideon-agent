import { act, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { GenUiWidget } from './GenUiWidget'
import { GenUiHostCtx } from './actions'
import { registerCoreGenUiComponents } from './components'
import { allComponents } from './registry'
import { SourcesComponent } from './advancedComponents'
import { WIDGET_ACTION_EVENT } from '../widget/actionTurn'

registerCoreGenUiComponents()

const envelope = (revision = 1, actionProps: Record<string, unknown> = {}) => ({
  schemaVersion: 2,
  id: 'purchase-options',
  revision,
  root: 'root',
  elements: {
    root: { type: 'Stack', props: { direction: 'grid', gap: 'm' }, children: ['compare', 'timeline', 'sources', 'action'] },
    compare: {
      type: 'Compare',
      props: {
        title: 'Choose a plan',
        items: [
          { id: 'monthly', label: 'Monthly plan', description: 'Flexible billing', details: [{ label: 'Price', value: '$25' }] },
          { id: 'annual', label: 'Annual plan', description: 'Best long-term value', details: [{ label: 'Price', value: '$240' }, { label: 'Seats', value: '5' }] },
        ],
      },
    },
    timeline: {
      type: 'Timeline',
      props: {
        title: 'Rollout',
        items: [
          { id: 'review', label: 'Review terms', status: 'done', time: 'Today' },
          { id: 'confirm', label: 'Confirm purchase', status: 'active', description: 'Requires explicit confirmation' },
        ],
      },
    },
    sources: {
      type: 'Sources',
      props: {
        title: 'References',
        items: [{ id: 'terms', label: 'Plan terms', url: 'https://example.com/terms', description: 'Current published terms' }],
      },
    },
    action: {
      type: 'ActionPreview',
      props: {
        title: 'Purchase plan',
        action: 'purchase_plan',
        label: 'Purchase selected plan',
        description: 'Review the grounded values before sending.',
        payload: { currency: 'USD' },
        fields: ['notes'],
        selectionFrom: 'compare',
        selectionField: 'plan_id',
        confirmLabel: 'Confirm purchase',
        ...actionProps,
      },
    },
  },
  state: {},
})

const host = { producer: { kind: 'chat' as const }, scopeId: 'runtime-one', conversationId: 'conversation-one' }
const published: Array<{ text: string; label?: string }> = []
function recordAction(event: Event) {
  const detail = (event as CustomEvent).detail as { text: string; label?: string }
  published.push(detail)
}

beforeEach(() => {
  localStorage.clear()
  published.length = 0
  window.addEventListener(WIDGET_ACTION_EVENT, recordAction)
})
afterEach(() => window.removeEventListener(WIDGET_ACTION_EVENT, recordAction))

describe('GenUI v2 native component palette', () => {
  it('registers all 15 native components and renders the grounded palette', () => {
    expect(allComponents().map(component => component.name)).toEqual(expect.arrayContaining([
      'Stack', 'Card', 'StatTile', 'Table', 'List', 'Bar', 'Callout', 'Badge', 'ProgressBar', 'Button', 'Form',
      'Compare', 'Timeline', 'Sources', 'ActionPreview',
    ]))

    const { getByText, getByRole } = render(
      <GenUiHostCtx.Provider value={host}>
        <GenUiWidget content={JSON.stringify(envelope())} title="Plan decision" />
      </GenUiHostCtx.Provider>,
    )

    expect(getByText('Monthly plan')).toBeTruthy()
    expect(getByText('Confirm purchase')).toBeTruthy()
    expect(getByText('Review terms')).toBeTruthy()
    const source = getByRole('link', { name: /Plan terms/ })
    expect(source.getAttribute('href')).toBe('https://example.com/terms')
    expect(source.getAttribute('rel')).toContain('noopener')
  })

  it('keeps filtering and selection local, then dispatches only after review and confirmation', async () => {
    const view = render(
      <GenUiHostCtx.Provider value={host}>
        <GenUiWidget content={JSON.stringify(envelope())} title="Plan decision" />
      </GenUiHostCtx.Provider>,
    )

    const review = view.getByRole('button', { name: 'Review action' })
    expect(review).toBeDisabled()

    fireEvent.change(view.getByRole('textbox', { name: 'Filter comparison options' }), { target: { value: 'annual' } })
    expect(view.queryByText('Monthly plan')).toBeNull()
    expect(published).toEqual([])

    fireEvent.click(view.getByRole('radio', { name: /Annual plan/ }))
    expect(view.getByRole('radio', { name: /Annual plan/ })).toBeChecked()
    expect(published).toEqual([])

    fireEvent.change(view.getByRole('textbox', { name: 'Notes' }), { target: { value: 'Use the operations card' } })
    fireEvent.click(view.getByRole('button', { name: 'Review action' }))
    expect(view.getByText('Review action', { selector: 'p' })).toBeTruthy()
    expect(view.getAllByText('Annual plan').length).toBeGreaterThan(1)
    expect(view.getAllByText('$240').length).toBeGreaterThan(1)
    expect(published).toEqual([])

    fireEvent.click(view.getByRole('button', { name: 'Cancel' }))
    expect(view.queryByRole('button', { name: 'Confirm purchase' })).toBeNull()
    expect(published).toEqual([])

    fireEvent.click(view.getByRole('button', { name: 'Review action' }))
    view.rerender(
      <GenUiHostCtx.Provider value={host}>
        <GenUiWidget content={JSON.stringify(envelope(2, {
          action: 'upgrade_plan', payload: { currency: 'EUR' }, fields: ['notes', 'owner'],
        }))} title="Plan decision" />
      </GenUiHostCtx.Provider>,
    )
    expect(view.queryByRole('button', { name: 'Confirm purchase' })).toBeNull()
    expect(view.getByRole('button', { name: 'Review action' })).toBeTruthy()
    expect(view.getByRole('textbox', { name: 'Notes' })).toHaveValue('Use the operations card')
    expect(view.getByRole('textbox', { name: 'Owner' })).toHaveValue('')

    fireEvent.change(view.getByRole('textbox', { name: 'Owner' }), { target: { value: 'Finance' } })
    fireEvent.click(view.getByRole('button', { name: 'Review action' }))
    act(() => {
      view.getByRole('button', { name: 'Confirm purchase' }).click()
      view.getByRole('button', { name: 'Confirm purchase' }).click()
    })

    await waitFor(() => expect(published).toHaveLength(1))
    expect(published[0].label).toBe('Purchase selected plan')
    expect(published[0].text).toContain('upgrade_plan')
    expect(published[0].text).toContain('"plan_id":"annual"')
    expect(published[0].text).toContain('"notes":"Use the operations card"')
    expect(published[0].text).toContain('"owner":"Finance"')
    expect(published[0].text).toContain('"currency":"EUR"')
    expect(await view.findByText('Queued for this conversation.')).toBeTruthy()

    view.rerender(
      <GenUiHostCtx.Provider value={host}>
        <GenUiWidget content={JSON.stringify(envelope(3, {
          action: 'upgrade_plan', payload: { currency: 'EUR' }, fields: ['notes', 'owner'],
        }))} title="Plan decision" />
      </GenUiHostCtx.Provider>,
    )
    expect(view.getByRole('radio', { name: /Annual plan/ })).toBeChecked()
    expect(view.getByRole('textbox', { name: 'Notes' })).toHaveValue('Use the operations card')
  })

  it('renders only HTTP and HTTPS source links even when called defensively', () => {
    const { getByRole, queryByText } = render(<SourcesComponent args={{
      items: [
        { id: 'safe', label: 'Safe source', url: 'http://example.com/a' },
        { id: 'unsafe', label: 'Unsafe source', url: 'javascript:alert(1)' },
      ],
    }} children={{}} />)

    expect(getByRole('link', { name: /Safe source/ }).getAttribute('href')).toBe('http://example.com/a')
    expect(queryByText('Unsafe source')).toBeNull()
  })
})
