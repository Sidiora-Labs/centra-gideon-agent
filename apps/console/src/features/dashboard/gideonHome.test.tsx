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

  it('gives the intro usable desktop width and keeps actions on one row', () => {
    render(<GideonHomeIntro navigate={vi.fn()} />)
    const heading = screen.getByRole('heading', { name: 'What would you like to do?' })
    expect(heading.parentElement?.style.maxWidth).toBe('44rem')
    const actions = screen.getByRole('button', { name: 'New conversation' }).parentElement
    expect(actions?.classList.contains('sm:flex-row')).toBe(true)
    expect(screen.getByRole('button', { name: 'New conversation' }).classList.contains('whitespace-nowrap')).toBe(true)
    expect(screen.getByRole('button', { name: 'Explore apps' }).classList.contains('whitespace-nowrap')).toBe(true)
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
    expect(screen.queryByRole('heading', { name: 'Activity' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'System status' })).toBeNull()
    expect(screen.queryByRole('button', { name: /loops running/i })).toBeNull()
    await userEvent.click(screen.getByText('Your overview'))
    expect(screen.getByText('Dashboard composition')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Activity' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'System status' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /loops running/i })).toBeTruthy()
  })

  it('opens the assistant bubble and starts a real conversation route', async () => {
    const navigate = vi.fn()
    render(<DashboardPage sub="" query={{}} navEpoch={0} navigate={navigate} setQuery={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Open the assistant' }))
    expect(screen.getByText('How can I help?')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Close the assistant' }).getAttribute('aria-expanded')).toBe('true')
    await userEvent.click(screen.getByRole('button', { name: 'Start a conversation' }))
    expect(navigate).toHaveBeenCalledExactlyOnceWith('chat/new')
  })

  it('closes the assistant bubble without starting a conversation', async () => {
    const navigate = vi.fn()
    render(<DashboardPage sub="" query={{}} navEpoch={0} navigate={navigate} setQuery={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Open the assistant' }))
    await userEvent.click(screen.getByRole('button', { name: 'Close the assistant' }))
    expect(screen.queryByText('How can I help?')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Start a conversation' })).toBeNull()
    expect(navigate).not.toHaveBeenCalled()
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
