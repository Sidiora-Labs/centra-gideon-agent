import { useState } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { BookOpen, CalendarDays, UsersRound } from 'lucide-react'
import { AreaNavigation, type AreaDestination } from './AreaNavigation'

const destinations: readonly AreaDestination[] = [
  { id: 'people', label: 'People', icon: UsersRound, group: 'Communication' },
  { id: 'calendar', label: 'Calendar', icon: CalendarDays, group: 'Communication' },
  { id: 'reading', label: 'Reading', icon: BookOpen, group: 'Reference' },
]

function NavigationHarness() {
  const [active, setActive] = useState('people')
  return <AreaNavigation label="Workspace destinations" items={destinations} active={active} onChange={setActive}>
    <label>Working note<input defaultValue="" /></label>
    <output aria-label="Active destination">{active}</output>
  </AreaNavigation>
}

afterEach(cleanup)

describe('AreaNavigation', () => {
  it('publishes one selected destination and changes it through native keyboard activation', async () => {
    const user = userEvent.setup()
    render(<NavigationHarness />)

    const people = screen.getByRole('button', { name: 'People' })
    const calendar = screen.getByRole('button', { name: 'Calendar' })
    const reading = screen.getByRole('button', { name: 'Reading' })
    expect(people).toHaveAttribute('aria-pressed', 'true')
    expect(calendar).toHaveAttribute('aria-pressed', 'false')
    expect(reading).toHaveAttribute('aria-pressed', 'false')

    people.focus()
    await user.keyboard('{Tab}{Enter}')
    expect(calendar).toHaveFocus()
    expect(calendar).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Active destination')).toHaveTextContent('calendar')

    await user.keyboard('{Tab} ')
    expect(reading).toHaveFocus()
    expect(reading).toHaveAttribute('aria-pressed', 'true')
    expect(calendar).toHaveAttribute('aria-pressed', 'false')
  })

  it('preserves child work while the selected destination changes', async () => {
    const user = userEvent.setup()
    render(<NavigationHarness />)
    const note = screen.getByLabelText('Working note')
    await user.type(note, 'Call Sam after the meeting')

    await user.click(screen.getByRole('button', { name: 'Calendar' }))

    expect(screen.getByLabelText('Working note')).toBe(note)
    expect(note).toHaveValue('Call Sam after the meeting')
    expect(screen.getByRole('navigation', { name: 'Workspace destinations' })).toContainElement(screen.getByRole('button', { name: 'Calendar' }))
    expect(screen.getByRole('button', { name: 'Calendar' })).toHaveAttribute('aria-pressed', 'true')
  })
  it('opens labeled sections and closes them after navigation without clearing work', async () => {
    const user = userEvent.setup()
    render(<NavigationHarness />)
    await user.type(screen.getByLabelText('Working note'), 'Keep this draft')
    await user.click(screen.getByRole('button', { name: 'All sections' }))
    expect(screen.getByRole('button', { name: 'Close sections' })).toHaveAttribute('aria-expanded', 'true')
    await user.click(screen.getByRole('button', { name: 'Reading' }))
    expect(screen.getByRole('button', { name: 'All sections' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByLabelText('Active destination')).toHaveTextContent('reading')
    expect(screen.getByLabelText('Working note')).toHaveValue('Keep this draft')
  })

})
