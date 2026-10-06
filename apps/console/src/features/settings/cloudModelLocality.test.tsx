import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { OffMachineChip } from './OffMachineChip'

describe('execution locality chip', () => {
  it('explains remote execution only when the server declares it', () => {
    const view = render(<OffMachineChip runsHere={false} />)
    expect(screen.getByText('off this machine')).toBeTruthy()
    view.rerender(<OffMachineChip runsHere={true} />)
    expect(screen.queryByText('off this machine')).toBeNull()
    view.rerender(<OffMachineChip />)
    expect(screen.queryByText('off this machine')).toBeNull()
  })
})
