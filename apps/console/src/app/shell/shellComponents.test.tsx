import { expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { Search } from 'lucide-react'
import { CommandPalette, type Command } from './CommandPalette'
import { initialPalette, paletteReducer, searchCommands } from './paletteState'
import { initialSetup, setupReducer } from './onboardingState'
import { incidentReducer, initialIncident } from './incidentState'
import { canReloadChunk, isChunkLoadError } from './errorRecovery'
import { ErrorBoundary } from './ErrorBoundary'

it('keeps command ranking stable across prefix, contained label and keyword matches', () => {
  const command = (id: string, label: string, keywords?: string): Command => ({ id, label, keywords, icon: Search, run: () => {} })
  const commands = [command('keyword', 'Run a task', 'tools'), command('contained', 'All tools'), command('prefix1', 'Tools'), command('prefix2', 'Tools settings')]
  expect(searchCommands(commands, ' tools ').map(({ id }) => id)).toEqual(['prefix1', 'prefix2', 'contained', 'keyword'])
  expect(paletteReducer(initialPalette, { type: 'move', delta: 1, count: 0 }).cursor).toBe(0)
})
it('runs the selected command from its named modal and restores focus on escape', () => {
  let selected = ''
  render(<><button type="button">Open commands</button><CommandPalette commands={['First', 'Second'].map((label) => ({ id: label, label, icon: Search, run: () => { selected = label } }))} /></>)
  const trigger = screen.getByRole('button', { name: 'Open commands' })
  trigger.focus()
  fireEvent.keyDown(window, { ctrlKey: true, key: 'k' })
  expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeTruthy()
  const search = screen.getByLabelText('Search pages and actions')
  fireEvent.keyDown(search, { key: 'ArrowDown' })
  expect(screen.getByRole('option', { name: 'Second' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.keyDown(search, { key: 'Enter' })
  expect(selected).toBe('Second')
  expect(screen.queryByRole('dialog')).toBeNull()
  trigger.focus()
  fireEvent.keyDown(window, { metaKey: true, key: 'k' })
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(trigger).toHaveFocus()
})
it('does not launch a command from an empty result set and resets selection with a query', () => {
  let count = 0
  render(<CommandPalette commands={[{ id: 'one', label: 'One', icon: Search, run: () => { count += 1 } }]} />)
  fireEvent.keyDown(window, { ctrlKey: true, key: 'k' })
  const search = screen.getByLabelText('Search pages and actions')
  fireEvent.change(search, { target: { value: 'missing' } })
  fireEvent.keyDown(search, { key: 'ArrowDown' })
  fireEvent.keyDown(search, { key: 'Enter' })
  expect(count).toBe(0)
  expect(search).not.toHaveAttribute('aria-activedescendant')
  fireEvent.change(search, { target: { value: 'One' } })
  expect(screen.getByRole('option', { name: 'One' })).toHaveAttribute('aria-selected', 'true')
})
it('keeps the current setup step when persisted readiness arrives late', () => {
  let state = setupReducer(initialSetup, { type: 'draft', value: ' Ada ' })
  state = setupReducer(state, { type: 'name' })
  expect(state.step).toBe('import')
  state = setupReducer(state, { type: 'loaded', value: { needs_model: false, has_model_provider: true, has_chat_binding: true, step: 'first_success', first_success: { knowledge: true, trigger: false, loop: false } } })
  expect(state.step).toBe('import')
  state = setupReducer(state, { type: 'try', summary: 'Skipped' })
  expect(state).toMatchObject({ name: 'Ada', step: 'ready', tried: '1 of 3 tried' })
})
it('rejects old incident snapshots after a successful resume and preserves state on failure', () => {
  let state = incidentReducer(initialIncident, { type: 'snapshot', revision: 1, active: true, reason: 'disk full' })
  state = incidentReducer(state, { type: 'resume', revision: 3 })
  expect(incidentReducer(state, { type: 'snapshot', revision: 2, active: true, reason: 'old' })).toBe(state)
  const failed = incidentReducer(state, { type: 'settled', revision: 3, ok: false })
  expect(failed).toMatchObject({ active: true, busy: false, reason: 'disk full' })
  const resumed = incidentReducer(state, { type: 'settled', revision: 3, ok: true })
  expect(incidentReducer(resumed, { type: 'snapshot', revision: 2, active: true, reason: 'old' })).toBe(resumed)
  expect(resumed.active).toBe(false)
})
it('recognizes browser chunk failures and enforces the reload guard interval', () => {
  expect(isChunkLoadError(new Error('Failed to fetch dynamically imported module: /assets/old.js'))).toBe(true)
  expect(isChunkLoadError({ name: 'ChunkLoadError' })).toBe(true)
  expect(isChunkLoadError(new Error('An authored message'))).toBe(false)
  expect(canReloadChunk(20_000, '15000')).toBe(false)
  expect(canReloadChunk(25_000, '15000')).toBe(true)
  expect(canReloadChunk(25_000, null)).toBe(true)
})
it('a changed route reset key restores content after a rendering failure', () => {
  function Page({ broken }: { broken: boolean }) { if (broken) throw new Error('The view failed'); return <p>Recovered route</p> }
  const view = render(<ErrorBoundary resetKey="old"><Page broken /></ErrorBoundary>)
  expect(screen.getByRole('heading', { name: 'This page hit an error' })).toBeTruthy()
  act(() => view.rerender(<ErrorBoundary resetKey="new"><Page broken={false} /></ErrorBoundary>))
  expect(screen.getByText('Recovered route')).toBeTruthy()
})
