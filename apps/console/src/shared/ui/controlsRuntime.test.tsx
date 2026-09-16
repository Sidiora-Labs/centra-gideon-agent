import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Copy, List, Grid2X2 } from 'lucide-react'
import { Button } from './Button'
import { IconButton } from './IconButton'
import { QuietButton } from './QuietButton'
import { SquareIconButton } from './SquareIconButton'
import { TileButton } from './TileButton'
import { Toggle } from './Toggle'
import { Segmented } from './Segmented'
import { Field } from './forms'
import { controlAvailability, pointerPercent, segmentTarget } from './controlState'
import { segmentNeedsMenu } from './segmentedLayout'

describe('shared action availability', () => {
  it('distinguishes working, explained unavailability, and native disabling', () => {
    expect(controlAvailability()).toEqual({ blocked: false, nativeDisabled: false, ariaDisabled: undefined, busy: undefined })
    expect(controlAvailability(true)).toMatchObject({ blocked: true, nativeDisabled: true, ariaDisabled: undefined })
    expect(controlAvailability(true, false, 'Choose a project')).toMatchObject({ blocked: true, nativeDisabled: false, ariaDisabled: true })
    expect(controlAvailability(false, true, 'Choose a project')).toMatchObject({ blocked: true, nativeDisabled: true, ariaDisabled: undefined, busy: true })
    expect(controlAvailability(true, true, 'Choose a project', true)).toMatchObject({ blocked: true, nativeDisabled: false, ariaDisabled: true, busy: true })
  })

  it('prevents an explained disabled submit from submitting its form and becomes live when enabled', () => {
    const submitted: string[] = []
    const view = (disabled: boolean) => <form onSubmit={(event) => { event.preventDefault(); submitted.push('submitted') }}>
      <Button type="submit" disabled={disabled} disabledReason="Choose a project">Create</Button>
    </form>
    const { rerender } = render(view(true))
    const button = screen.getByRole('button', { name: 'Create' })
    button.focus()
    expect(document.activeElement).toBe(button)
    fireEvent.click(button)
    expect(submitted).toEqual([])
    rerender(view(false))
    fireEvent.click(button)
    expect(submitted).toEqual(['submitted'])
    expect(button).not.toHaveAttribute('title')
    expect(button).not.toHaveAttribute('aria-disabled')
  })

  it('keeps a busy action name, exposes its progress text visually, and blocks duplicate activation', () => {
    const activated: string[] = []
    const view = (busy: boolean) => <Button loading={busy} loadingLabel="Synchronizing…" onClick={() => activated.push('sync')}>Sync now</Button>
    const { rerender } = render(view(true))
    const button = screen.getByRole('button', { name: 'Sync now' })
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toBeDisabled()
    expect(screen.getByText('Synchronizing…').closest('[aria-hidden]')).not.toBeNull()
    expect(button.classList.contains('disabled:opacity-40')).toBe(false)
    fireEvent.click(button)
    expect(activated).toEqual([])
    rerender(view(false))
    fireEvent.click(button)
    expect(activated).toEqual(['sync'])
    expect(button).not.toHaveAttribute('aria-busy')
  })

  it('preserves action names and custom tooltips across icon-control states', () => {
    const events: string[] = []
    render(<><IconButton icon={Copy} label="Copy selection" title="Copy" disabled disabledReason="Select text" onClick={() => events.push('round')} />
      <SquareIconButton icon={Copy} label="Copy file" title="Copy" disabled disabledReason="Choose a file" onClick={() => events.push('square')} /></>)
    const round = screen.getByRole('button', { name: 'Copy selection' })
    const square = screen.getByRole('button', { name: 'Copy file' })
    expect(round).toHaveAttribute('title', 'Copy — Select text')
    expect(square).toHaveAttribute('title', 'Copy — Choose a file')
    for (const button of [round, square]) {
      button.focus()
      expect(document.activeElement).toBe(button)
      fireEvent.click(button)
      expect(button).not.toBeDisabled()
    }
    expect(events).toEqual([])
  })

  it('keeps disclosure and selection semantics separate', () => {
    render(<><SquareIconButton label="Configure" on ariaExpanded={false}><Copy /></SquareIconButton>
      <QuietButton ariaExpanded={true}>Details</QuietButton>
      <TileButton active ariaLabel="Ocean appearance">Preview content</TileButton>
      <IconButton icon={Copy} label="Pin" active={false} /></>)
    expect(screen.getByRole('button', { name: 'Configure' })).not.toHaveAttribute('aria-pressed')
    expect(screen.getByRole('button', { name: 'Configure' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: 'Details' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Ocean appearance' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Pin' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('constrains pointer feedback to finite in-bounds coordinates', () => {
    expect(pointerPercent(30, 10, 40)).toBe(50)
    expect(pointerPercent(-10, 10, 40)).toBe(0)
    expect(pointerPercent(200, 10, 40)).toBe(100)
    expect(pointerPercent(20, 10, 0)).toBe(50)
  })
})

describe('switch rendering contracts', () => {
  it('keeps the reason ahead of the field hint while gated and restores the hint when enabled', () => {
    const values: boolean[] = []
    const view = (disabled: boolean) => <Field label="Notifications" hint="Receive task results">
      <Toggle label="Enable notifications" on={false} onChange={(value) => values.push(value)} disabled={disabled} disabledReason="Connect first" />
    </Field>
    const { rerender } = render(view(true))
    const toggle = screen.getByRole('switch', { name: 'Enable notifications' })
    expect(toggle).toHaveAttribute('title', 'Connect first')
    expect(toggle).not.toHaveAttribute('aria-describedby')
    fireEvent.click(toggle)
    expect(values).toEqual([])
    rerender(view(false))
    const hintId = toggle.getAttribute('aria-describedby')!
    expect(document.getElementById(hintId)).toHaveTextContent('Receive task results')
    expect(toggle).not.toHaveAttribute('title')
    fireEvent.click(toggle)
    expect(values).toEqual([true])
  })

  it('renders a standalone read-only switch or a purely decorative indicator without nested buttons', () => {
    const { container } = render(<><Toggle label="Read only" on readOnly /><button aria-label="Enable row"><Toggle on decorative /></button></>)
    expect(screen.getAllByRole('switch')).toHaveLength(1)
    expect(screen.getByRole('switch', { name: 'Read only' }).tagName).toBe('SPAN')
    expect(screen.getByRole('button', { name: 'Enable row' }).querySelector('button')).toBeNull()
    expect(container.querySelector('[aria-hidden="true"]')).not.toBeNull()
  })
})

describe('segmented selection and responsive sizing', () => {
  const options = [{ key: 'list', label: 'List', icon: List }, { key: 'grid', label: 'Grid', icon: Grid2X2 }, { key: 'detail', label: 'Detail' }]
  function Selection({ disabled = false, value = 'list', iconOnly = false }: { disabled?: boolean; value?: string; iconOnly?: boolean }) {
    const [selected, select] = useState(value)
    return <Segmented options={options} value={selected} onChange={select} ariaLabel="Display" disabled={disabled} iconOnly={iconOnly} />
  }

  it('moves selection and focus together for arrows, Home and End, wrapping both ends', () => {
    render(<Selection />)
    const list = screen.getByRole('tab', { name: 'List' })
    const grid = screen.getByRole('tab', { name: 'Grid' })
    const detail = screen.getByRole('tab', { name: 'Detail' })
    fireEvent.keyDown(list, { key: 'ArrowRight' })
    expect(grid).toHaveAttribute('aria-selected', 'true')
    expect(document.activeElement).toBe(grid)
    fireEvent.keyDown(grid, { key: 'End' })
    expect(document.activeElement).toBe(detail)
    fireEvent.keyDown(detail, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(list)
    fireEvent.keyDown(list, { key: 'ArrowLeft' })
    expect(document.activeElement).toBe(detail)
    fireEvent.keyDown(detail, { key: 'Home' })
    expect(list).toHaveAttribute('tabindex', '0')
    expect(grid).toHaveAttribute('tabindex', '-1')
    expect(detail).toHaveAttribute('tabindex', '-1')
  })

  it('does not change a disabled selection even for dispatched keyboard events', () => {
    render(<Selection disabled />)
    const list = screen.getByRole('tab', { name: 'List' })
    fireEvent.keyDown(list, { key: 'ArrowRight' })
    fireEvent.click(screen.getByRole('tab', { name: 'Grid' }))
    expect(list).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Grid' })).toHaveAttribute('aria-selected', 'false')
  })

  it('keeps icon-only tabs named and a missing selection reachable', () => {
    render(<Selection iconOnly value="removed" />)
    const list = screen.getByRole('tab', { name: 'List' })
    expect(list).toHaveAttribute('tabindex', '0')
    expect(list).toHaveAttribute('aria-selected', 'false')
    fireEvent.keyDown(list, { key: 'End' })
    expect(screen.getByRole('tab', { name: 'Detail' })).toHaveAttribute('aria-selected', 'true')
  })

  it('compares intrinsic width with the available sibling-adjusted slot in both directions', () => {
    expect(segmentNeedsMenu(200, 180, [30, 20])).toBe(true)
    expect(segmentNeedsMenu(200, 300, [30, 20])).toBe(false)
    expect(segmentNeedsMenu(200, 250, [30, 20])).toBe(false)
    expect(segmentNeedsMenu(200.9, 250, [30, 20])).toBe(false)
    expect(segmentNeedsMenu(202, 250, [30, 20])).toBe(true)
    expect(segmentNeedsMenu(0, 10, [30])).toBe(false)
    expect(segmentTarget('ArrowDown', 0, 0)).toBeNull()
    expect(segmentTarget('Enter', 0, 3)).toBeNull()
  })

  it('retains a scroll container and handles an empty option set', () => {
    const { container } = render(<Segmented options={[]} value="" onChange={() => {}} collapse="scroll" ariaLabel="Empty choices" />)
    expect(screen.getByRole('tablist', { name: 'Empty choices' })).toBeInTheDocument()
    expect(screen.queryAllByRole('tab')).toHaveLength(0)
    expect(container.firstElementChild).toHaveClass('overflow-x-auto')
  })
})
