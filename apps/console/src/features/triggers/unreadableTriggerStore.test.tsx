import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { UnreadableNotice } from './UnreadableNotice'

// The routes supply the wording; the native notice renders it as text.
describe('unreadable automation store notice', () => {
  it('discloses the file, preserved copy and remedy in an accessible alert', () => {
    render(<UnreadableNotice sources={[{
      file: '/scratch/triggers.json',
      said: '/scratch/triggers.json could not be read. A copy is kept at /scratch/triggers.json.broken-20261006.',
      remedy: 'Repair the file, then reload this page.',
    }]} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Your automations could not be read')
    expect(alert).toHaveTextContent('/scratch/triggers.json.broken-20261006')
    expect(alert).toHaveTextContent('Repair the file, then reload this page.')
  })

  it('removes the failure after a successful recovery', () => {
    const view = render(<UnreadableNotice sources={[{ file: 'triggers.json', said: 'Cannot read', remedy: 'Repair' }]} />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    view.rerender(<UnreadableNotice sources={[]} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
