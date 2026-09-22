import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'
import { ToggleRow } from './settingsUI'

describe('ToggleRow confirmation', () => {
  it('only writes a weakening change after confirmation, while tightening writes immediately', async () => {
    const patch = vi.fn()
    const weakening = {
      title: 'Relax this safety default?',
      confirmLabel: 'Relax safety',
      danger: true,
    }
    const { container, rerender } = render(<><ToggleRow label="Safety" cfg={{ safety: false }} field="safety" patch={patch as never}
      confirmOn={(next) => next ? weakening : undefined} /><DialogHost /></>)

    fireEvent.click(container.querySelector('[role="switch"]')!)
    const dialog = await screen.findByRole('alertdialog', { name: weakening.title })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(patch).not.toHaveBeenCalled())

    fireEvent.click(container.querySelector('[role="switch"]')!)
    const accepted = await screen.findByRole('alertdialog', { name: weakening.title })
    fireEvent.click(within(accepted).getByRole('button', { name: weakening.confirmLabel }))
    await waitFor(() => expect(patch).toHaveBeenCalledWith('safety', true, expect.any(Function), 'Safety'))

    rerender(<><ToggleRow label="Safety" cfg={{ safety: true }} field="safety" patch={patch as never}
      confirmOn={(next) => next ? weakening : undefined} /><DialogHost /></>)
    fireEvent.click(container.querySelector('[role="switch"]')!)
    expect(patch).toHaveBeenLastCalledWith('safety', false, expect.any(Function), 'Safety')
  })
})
