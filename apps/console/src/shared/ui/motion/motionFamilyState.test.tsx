import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ActivationCompletion, boundedMenuPoint, liquidOutline, reconcileReorder } from './motionFamilyState'
import { ContextMenu } from './ContextMenu'
import { Disintegrate } from './Disintegrate'
import { Expandable } from './Expandable'
import { Reorderable } from './Reorderable'
import { Bud } from './Bud'
import { EntranceGroup, EntranceRegion } from './Entrance'

const pause = (milliseconds: number) => new Promise<void>(resolve => setTimeout(resolve, milliseconds))

describe('menu placement', () => {
  it('keeps both edges inside the viewport for ordinary and negative pointer positions', () => {
    const viewport = { width: 800, height: 600 }
    const size = { width: 220, height: 160 }
    expect(boundedMenuPoint({ x: 780, y: 590 }, viewport, size)).toEqual({ x: 572, y: 432 })
    expect(boundedMenuPoint({ x: -20, y: -30 }, viewport, size)).toEqual({ x: 8, y: 8 })
    expect(boundedMenuPoint({ x: 120, y: 130 }, viewport, size)).toEqual({ x: 120, y: 130 })
  })
  it('retains a reachable leading edge when the menu is larger than its viewport', () => {
    expect(boundedMenuPoint({ x: 100, y: 100 }, { width: 120, height: 80 }, { width: 220, height: 360 })).toEqual({ x: 8, y: 8 })
  })
})

describe('per-activation completion', () => {
  it('resolves once, ignores inactive notifications and rearms for a subsequent activation', () => {
    const completion = new ActivationCompletion()
    expect(completion.finish()).toBe(false)
    completion.update(true)
    expect(completion.finish()).toBe(true)
    completion.update(true)
    expect(completion.finish()).toBe(false)
    completion.update(false)
    expect(completion.finish()).toBe(false)
    completion.update(true)
    expect(completion.finish()).toBe(true)
  })
})

describe('locked-row reorder reconciliation', () => {
  const items = [{ id: 'a' }, { id: 'fixed' }, { id: 'b' }, { id: 'c' }]
  const key = (item: { id: string }) => item.id
  const canDrag = (item: { id: string }) => item.id !== 'fixed'
  it('fills movable slots in the proposed order while retaining the locked row', () => {
    const next = reconcileReorder(items, [items[3], items[2], items[0]], key, canDrag)
    expect(next.map(key)).toEqual(['c', 'fixed', 'b', 'a'])
    expect(next[1]).toBe(items[1])
    expect(next[0]).toBe(items[3])
    expect(items.map(key)).toEqual(['a', 'fixed', 'b', 'c'])
  })
  it('rejects unknown and duplicate keys without dropping omitted existing rows', () => {
    const next = reconcileReorder(items, [{ id: 'c' }, { id: 'unknown' }, { id: 'c' }, items[1]], key, canDrag)
    expect(next.map(key)).toEqual(['c', 'fixed', 'a', 'b'])
    expect(new Set(next)).toHaveProperty('size', 4)
    expect(next[0]).toBe(items[3])
  })
  it('keeps a wholly locked list and an empty list stable', () => {
    expect(reconcileReorder(items, [...items].reverse(), key, () => false)).toEqual(items)
    expect(reconcileReorder([], items, key, canDrag)).toEqual([])
  })
})

describe('closed liquid geometry', () => {
  it('uses exact endpoints for all three shapes and keeps sixteen closed cubic spans', () => {
    for (const shape of ['circle', 'blob', 'squircle'] as const) {
      const endpoint = liquidOutline('circle', shape, 1, 0, 1, 0)
      expect(endpoint).toBe(liquidOutline(shape, shape, 0, 0, 1, 0))
      expect(endpoint.match(/C/g)).toHaveLength(16)
      expect(endpoint.endsWith('Z')).toBe(true)
      const numbers = endpoint.match(/-?\d+\.\d+/g)!.map(Number)
      expect(numbers.every(Number.isFinite)).toBe(true)
      expect(Math.min(...numbers)).toBeGreaterThan(0)
      expect(Math.max(...numbers)).toBeLessThan(100)
    }
  })
  it('removes shape departure at zero amplitude and removes time dependence without breathe', () => {
    expect(liquidOutline('circle', 'blob', .4, 0, 0, 0)).toBe(liquidOutline('squircle', 'blob', .8, 0, 0, 0))
    expect(liquidOutline('blob', 'squircle', .5, 0, 1, 0)).toBe(liquidOutline('blob', 'squircle', .5, 2, 1, 0))
    expect(liquidOutline('blob', 'squircle', .5, 0, 1, .055)).not.toBe(liquidOutline('blob', 'squircle', .5, 2, 1, .055))
  })
})

function MenuWorkspace({ disabled = false }: { disabled?: boolean }) {
  const [selected, setSelected] = useState('none')
  return <>
    <ContextMenu disabled={disabled} items={[
      { label: 'Unavailable action', disabled: true, onSelect: () => setSelected('unavailable') },
      { label: 'Choose row', onSelect: () => setSelected('chosen') },
    ]}><button data-ctx-anchor type="button">Open actions</button></ContextMenu>
    <button type="button">Elsewhere</button><output aria-label="Action result">{selected}</output>
  </>
}

describe('context menu interaction ownership', () => {
  it('clamps a pointer opening and activates one enabled action before returning focus', () => {
    render(<MenuWorkspace />)
    const invoker = screen.getByRole('button', { name: 'Open actions' })
    invoker.focus()
    fireEvent.contextMenu(invoker, { clientX: -40, clientY: -20 })
    expect(screen.getByRole('menu')).toHaveStyle({ left: '8px', top: '8px' })
    fireEvent.click(screen.getByRole('menuitem', { name: 'Unavailable action' }))
    expect(screen.getByLabelText('Action result')).toHaveTextContent('none')
    fireEvent.keyDown(document, { key: 'End' })
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Choose row' }))
    fireEvent.click(document.activeElement!)
    expect(screen.getByLabelText('Action result')).toHaveTextContent('chosen')
    expect(document.activeElement).toBe(invoker)
  })
  it('does not reclaim focus from an outside click', () => {
    render(<MenuWorkspace />)
    const invoker = screen.getByRole('button', { name: 'Open actions' })
    invoker.focus()
    fireEvent.keyDown(invoker, { key: 'ContextMenu' })
    const outside = screen.getByRole('button', { name: 'Elsewhere' })
    outside.focus()
    fireEvent.mouseDown(outside)
    expect(document.activeElement).toBe(outside)
  })
  it('opens from a real long press and cancels a subsequent cancelled touch', async () => {
    const view = render(<MenuWorkspace />)
    const invoker = screen.getByRole('button', { name: 'Open actions' })
    fireEvent.touchStart(invoker, { touches: [{ clientX: 120, clientY: 80 }] })
    await act(() => pause(530))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    view.unmount()
    render(<MenuWorkspace />)
    const next = screen.getByRole('button', { name: 'Open actions' })
    fireEvent.touchStart(next, { touches: [{ clientX: 100, clientY: 80 }] })
    fireEvent.touchCancel(next)
    await act(() => pause(530))
    expect(screen.queryByRole('menu')).toBeNull()
  })
  it('keeps empty and disabled menus closed', async () => {
    render(<ContextMenu items={[]}><button type="button">Empty actions</button></ContextMenu>)
    fireEvent.contextMenu(screen.getByRole('button', { name: 'Empty actions' }))
    expect(screen.queryByRole('menu')).toBeNull()
    render(<MenuWorkspace disabled />)
    const disabled = screen.getByRole('button', { name: 'Open actions' })
    fireEvent.keyDown(disabled, { key: 'F10', shiftKey: true })
    fireEvent.touchStart(disabled, { touches: [{ clientX: 10, clientY: 10 }] })
    await act(() => pause(530))
    expect(screen.queryByRole('menu')).toBeNull()
  })
})

describe('motion composition with live children', () => {
  it('keeps an expandable header interactive through opening and completed collapse', async () => {
    function Disclosure() {
      const [open, setOpen] = useState(false)
      return <Expandable open={open} header={<button type="button" onClick={() => setOpen(value => !value)}>Toggle details</button>}><input aria-label="Detail value" defaultValue="draft" /></Expandable>
    }
    render(<Disclosure />)
    const header = screen.getByRole('button', { name: 'Toggle details' })
    expect(screen.queryByLabelText('Detail value')).toBeNull()
    fireEvent.click(header)
    expect(screen.getByLabelText('Detail value')).toHaveValue('draft')
    fireEvent.click(header)
    await waitFor(() => expect(screen.queryByLabelText('Detail value')).toBeNull())
    expect(screen.getByRole('button', { name: 'Toggle details' })).toBe(header)
  })
  it('runs a dissolve completion once even if the active content rerenders', async () => {
    const completions: string[] = []
    const done = () => completions.push('done')
    const view = render(<Disintegrate active={false} onDone={done}><span>First row</span></Disintegrate>)
    view.rerender(<Disintegrate active onDone={done}><span>First row</span></Disintegrate>)
    await waitFor(() => expect(completions).toEqual(['done']))
    view.rerender(<Disintegrate active onDone={done}><span>Updated row</span></Disintegrate>)
    await act(() => pause(50))
    expect(completions).toEqual(['done'])
    expect(screen.getByText('Updated row')).toBeInTheDocument()
  })
  it('preserves child controls in locked and draggable rows', () => {
    function Rows() {
      const [items, setItems] = useState([{ id: 'fixed' }, { id: 'moving' }])
      const [selected, setSelected] = useState('none')
      return <><Reorderable items={items} onReorder={setItems} getKey={item => item.id} canDrag={item => item.id !== 'fixed'} renderItem={item => <button type="button" onClick={() => setSelected(item.id)}>{item.id}</button>} /><output aria-label="Selected row">{selected}</output></>
    }
    render(<Rows />)
    fireEvent.click(screen.getByRole('button', { name: 'fixed' }))
    expect(screen.getByLabelText('Selected row')).toHaveTextContent('fixed')
    fireEvent.click(screen.getByRole('button', { name: 'moving' }))
    expect(screen.getByLabelText('Selected row')).toHaveTextContent('moving')
  })
  it('preserves entrance node identity and the bud’s trigger-facing origin', async () => {
    const view = render(<EntranceGroup><EntranceRegion><Bud from="top"><span>Panel one</span></Bud></EntranceRegion></EntranceGroup>)
    const region = view.container.querySelector('[data-entrance-region]')
    expect(view.container.querySelector<HTMLElement>('[data-bud]')?.style.transformOrigin).toContain('0%')
    await waitFor(() => expect((region as HTMLElement).style.opacity).toBe('1'))
    view.rerender(<EntranceGroup><EntranceRegion><Bud from="bottom"><span>Panel two</span></Bud></EntranceRegion></EntranceGroup>)
    expect(view.container.querySelector('[data-entrance-region]')).toBe(region)
    expect(screen.getByText('Panel two')).toBeInTheDocument()
  })
})
