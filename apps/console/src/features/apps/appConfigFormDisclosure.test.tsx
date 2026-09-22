import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AppConfigFields, type SchemaProp } from './appConfigForm'

afterEach(() => cleanup())

describe('app config advanced-field disclosure', () => {
  it('keeps required advanced fields visible and reveals optional ones from an announced disclosure', () => {
    const props: Record<string, SchemaProp> = {
      title: { type: 'string', 'x-meta': { label: 'Title' } },
      provider: { type: 'string', 'x-meta': { label: 'Provider', tags: ['advanced'] } },
      assignee: { type: 'string', 'x-meta': { label: 'Assignee', tags: ['advanced'] } },
    }
    render(<AppConfigFields appName="action" props={props} cur={{}} set={() => {}} required={['provider']} />)

    expect(screen.getByText('Title')).toBeTruthy()
    expect(screen.getByText('Provider *')).toBeTruthy()
    expect(screen.queryByText('Assignee')).toBeNull()

    const disclosure = screen.getByRole('button', { name: 'Advanced settings' })
    expect(disclosure.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(disclosure)
    expect(disclosure.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('Assignee')).toBeTruthy()
  })

  it('keeps an all-advanced schema flat for native tasks', () => {
    const props: Record<string, SchemaProp> = {
      storage_dir: { type: 'string', 'x-meta': { label: 'Storage Directory', tags: ['advanced'] } },
    }
    render(<AppConfigFields appName="native-tasks" props={props} cur={{}} set={() => {}} />)

    expect(screen.getByText('Storage Directory')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Advanced settings' })).toBeNull()
  })
})
