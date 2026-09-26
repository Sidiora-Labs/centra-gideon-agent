import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DashboardPage, GideonHomeIntro, OverviewDisclosure } from './DashboardPage'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Gideon home introduction', () => {
  it('offers a direct first conversation', async () => {
    const navigate = vi.fn()
    render(<GideonHomeIntro navigate={navigate} />)
    expect(screen.getByRole('heading', { name: 'What would you like to do?' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(navigate).toHaveBeenCalledExactlyOnceWith('chat/new')
  })

  it('opens the named app collection', async () => {
    const navigate = vi.fn()
    render(<GideonHomeIntro navigate={navigate} />)
    await userEvent.click(screen.getByRole('button', { name: 'Explore apps' }))
    expect(navigate).toHaveBeenCalledExactlyOnceWith('apps')
  })

  it('keeps both first actions keyboard operable', async () => {
    const navigate = vi.fn()
    render(<GideonHomeIntro navigate={navigate} />)
    screen.getByRole('button', { name: 'New conversation' }).focus()
    await userEvent.keyboard('{Enter}')
    await userEvent.tab()
    await userEvent.keyboard(' ')
    expect(navigate.mock.calls).toEqual([['chat/new'], ['apps']])
  })
})

describe('dashboard overview disclosure', () => {
  const key = 'gideon:dashboard:overview-open'

  it('starts calm with secondary work collapsed', () => {
    localStorage.removeItem(key)
    render(<OverviewDisclosure><button type="button">Open a task</button></OverviewDisclosure>)
    expect(screen.queryByRole('button', { name: 'Open a task' })).toBeNull()
    expect(screen.getByText('Your overview')).toBeTruthy()
  })

  it('reveals and operates retained widgets when expanded', async () => {
    localStorage.removeItem(key)
    const action = vi.fn()
    render(<OverviewDisclosure><button type="button" onClick={action}>Open a task</button></OverviewDisclosure>)
    await userEvent.click(screen.getByText('Your overview'))
    await userEvent.click(screen.getByRole('button', { name: 'Open a task' }))
    expect(action).toHaveBeenCalledOnce()
    expect(localStorage.getItem(key)).toBe('1')
  })

  it('closes on request and remembers that choice across remounts', async () => {
    localStorage.setItem(key, '1')
    const view = render(<OverviewDisclosure><button type="button">Open a task</button></OverviewDisclosure>)
    expect(screen.getByRole('button', { name: 'Open a task' })).toBeTruthy()
    await userEvent.click(screen.getByText('Your overview'))
    expect(screen.queryByRole('button', { name: 'Open a task' })).toBeNull()
    expect(localStorage.getItem(key)).toBe('0')
    view.unmount()
    render(<OverviewDisclosure><button type="button">Open a task</button></OverviewDisclosure>)
    expect(screen.queryByRole('button', { name: 'Open a task' })).toBeNull()
    localStorage.removeItem(key)
  })
})

describe('dashboard consumer', () => {
  it('shows critical live sections before revealing the saved overview', async () => {
    localStorage.removeItem('gideon:dashboard:overview-open')
    render(<DashboardPage sub="" query={{}} navEpoch={0} navigate={vi.fn()} setQuery={vi.fn()} />)
    expect(screen.getByText('Needs you')).toBeTruthy()
    expect(screen.getByText('Active work')).toBeTruthy()
    expect(screen.getByText('Your overview')).toBeTruthy()
    expect(screen.queryByText('Dashboard composition')).toBeNull()
    await userEvent.click(screen.getByText('Your overview'))
    expect(screen.getByText('Dashboard composition')).toBeTruthy()
  })
})

describe('overview when preference storage is unavailable', () => {
  it('opens and closes locally when the browser denies storage', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Storage denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage denied') })
    render(<OverviewDisclosure><button type="button">Open a task</button></OverviewDisclosure>)
    expect(screen.queryByRole('button', { name: 'Open a task' })).toBeNull()
    await userEvent.click(screen.getByText('Your overview'))
    expect(screen.getByRole('button', { name: 'Open a task' })).toBeTruthy()
    await userEvent.click(screen.getByText('Your overview'))
    expect(screen.queryByRole('button', { name: 'Open a task' })).toBeNull()
  })
})
