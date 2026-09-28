import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useHashRoute } from '../../app/shell/useHashRoute'
import { ChatContextLine } from './ChatContextLine'

function RoutedChatOrigin({ startedBy }: { startedBy?: { name: string; destination: string } }) {
  const { route, sub, navigate } = useHashRoute('dashboard')
  return <>
    <ChatContextLine startedBy={startedBy} navigate={navigate} />
    <output data-testid="current-route">{route}/{sub}</output>
  </>
}

describe('the chat header app origin', () => {
  beforeEach(() => history.replaceState(null, '', '#/dashboard'))

  it('names the initiating app and opens the supplied authorized destination', async () => {
    render(<RoutedChatOrigin startedBy={{ name: 'Notes', destination: 'app/notes' }} />)

    fireEvent.click(screen.getByRole('button', { name: 'Started by Notes — open app' }))
    expect(location.hash).toBe('#/app/notes')
    await waitFor(() => expect(screen.getByTestId('current-route')).toHaveTextContent('app/notes'))
  })

  it('does not render an origin when authorized app metadata is absent', () => {
    render(<RoutedChatOrigin />)
    expect(screen.queryByText(/^Started by /)).toBeNull()
  })

  it('does not render a partial origin without a destination', () => {
    render(<RoutedChatOrigin startedBy={{ name: 'Notes', destination: '' }} />)
    expect(screen.queryByText(/^Started by /)).toBeNull()
  })
})
