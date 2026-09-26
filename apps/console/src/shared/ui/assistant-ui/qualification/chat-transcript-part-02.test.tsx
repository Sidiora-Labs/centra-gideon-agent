import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MessageAssistant } from '../../chat/MessageAssistant'
import { MessageUser } from '../../chat/MessageUser'

describe('message wrapper continuity', () => {
  it('keeps caller-owned actions attached to the assistant answer', () => {
    const events: string[] = []
    render(<MessageAssistant actions={<button type="button" onClick={() => events.push('regenerate')}>Regenerate answer</button>}>
      <p>Stored answer</p>
    </MessageAssistant>)
    expect(screen.getByText('Stored answer')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate answer' }))
    expect(events).toEqual(['regenerate'])
  })

  it('keeps the persisted optimized prompt behind the user disclosure', () => {
    render(<MessageUser optimized="Expanded prompt sent to model">Original request</MessageUser>)
    expect(screen.getByText('Original request')).toBeTruthy()
    expect(screen.queryByText('Expanded prompt sent to model')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Sent an optimized version' }))
    expect(screen.getByText('Expanded prompt sent to model')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Optimized prompt sent to the model' }))
    expect(screen.queryByText('Expanded prompt sent to model')).toBeNull()
  })

  it('preserves the real file-open action in a user message', () => {
    const paths: string[] = []
    render(<MessageUser onFileClick={(path) => paths.push(path)}>Review src/main.tsx</MessageUser>)
    fireEvent.click(screen.getByRole('button', { name: 'main.tsx' }))
    expect(paths).toEqual(['src/main.tsx'])
  })
})
