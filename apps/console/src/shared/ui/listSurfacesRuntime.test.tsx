import { describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { Table, THead, Th, Td } from './Table'
import { CardGridSkeleton, FormSkeleton, ListSkeleton, LoadError, Skeleton } from './ListScaffold'
import { WindowedList, type WindowedListProps } from './WindowedList'
import { listKeyDestination, recordRowHeight, RowGeometry } from './windowGeometry'

const keys = Array.from({ length: 5000 }, (_, index) => `item:${index}`)
const identify = (key: string) => key
function List(props: Partial<WindowedListProps<string>>) {
  return <WindowedList items={keys} rowKey={identify} rowHeights="uniform" estimateRowHeight={40}
    noun="items" findHint="Use Search items." {...props}>
    {props.children ?? (key => <button>{key}</button>)}
  </WindowedList>
}

describe('row geometry', () => {
  it('selects deep contiguous windows with exact trailing gap accounting', () => {
    const model = new RowGeometry(keys, 40, 8, new Map())
    const range = model.visible(2000 * 48, 480, 8)
    expect(range).toEqual({ start: 1992, end: 2018 })
    const padding = model.padding(range)
    const rows = range.end - range.start
    expect(padding.paddingTop + rows * 40 + (rows - 1) * 8 + padding.paddingBottom).toBe(5000 * 40 + 4999 * 8)
  })

  it('matches a linear visibility oracle across variable measurements and boundary offsets', () => {
    const measured = new Map(keys.map((key, index) => [key, 20 + (index % 7) * 13]))
    const model = new RowGeometry(keys, 40, 6, measured)
    for (const top of [-200, 0, 19, 20, 26, 40, 2174, model.height - 40, model.height, model.height + 500]) {
      let start = 0
      while (start < keys.length && model.offsets[start + 1] <= top) start++
      let end = start
      while (end < keys.length && model.offsets[end] < top + 400) end++
      expect(model.visible(top, 400, 8)).toEqual({ start: Math.max(0, start - 8), end: Math.min(keys.length, end + 8) })
    }
  })

  it('keeps measurements keyed through sorting and rejects invalid or subpixel changes', () => {
    const measured = new Map<string, number>()
    expect(recordRowHeight(measured, 'second', 90)).toBe(true)
    expect(recordRowHeight(measured, 'second', 90.5)).toBe(false)
    expect(recordRowHeight(measured, 'second', Number.NaN)).toBe(false)
    expect(recordRowHeight(measured, 'second', 0)).toBe(false)
    expect(recordRowHeight(measured, 'second', Infinity)).toBe(false)
    const model = new RowGeometry(['second', 'first'], 40, 5, measured)
    expect([...model.offsets]).toEqual([0, 95, 140])
    expect(model.height).toBe(135)
    expect(model.rowBottom(0)).toBe(90)
    expect(model.positions.get('first')).toBe(1)
  })

  it('handles empty collections and bounded reveal without rendering the intervening rows', () => {
    expect(new RowGeometry([], 40, 8, new Map()).visible(0, 400, 8)).toEqual({ start: 0, end: 0 })
    expect(new RowGeometry(keys, 40, 0, new Map()).around(4000, 400, 8)).toEqual({ start: 3992, end: 4019 })
  })

  it.each([
    ['ArrowDown', -1, 0], ['ArrowUp', 0, 0], ['End', 0, 99], ['Home', 90, 0],
    ['PageDown', 97, 99], ['PageUp', 3, 0], ['PageDown', -1, 10], ['Escape', 20, undefined],
  ])('resolves %s from %s', (key, current, expected) => {
    expect(listKeyDestination(key as string, current as number, 100, 10)).toBe(expected)
  })
})

describe('list ownership in the real DOM', () => {
  it('gives repeated nouns distinct descriptions', () => {
    render(<><List /><List /></>)
    const ids = screen.getAllByRole('list').map(list => list.getAttribute('aria-describedby'))
    expect(new Set(ids).size).toBe(2)
    ids.forEach(id => expect(document.getElementById(id!)?.textContent).toContain('of 5000 items'))
  })

  it('waits for an unresolved anchor to arrive and focuses that row', () => {
    const view = render(<List items={['first']} anchorKey="later" />)
    view.rerender(<List items={['first', 'later']} anchorKey="later" />)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'later' }))
  })

  it('uses literal keys and a programmatic fallback for rows without controls', () => {
    const key = 'route/[a="b"]:#'
    render(<List items={['first', key]} anchorKey={key}>{item => <span>{item}</span>}</List>)
    expect(document.activeElement?.getAttribute('data-row-key')).toBe(key)
    expect(document.activeElement?.getAttribute('tabindex')).toBe('-1')
  })

  it('does not take editing keys or keys already handled by a child', () => {
    render(<List items={['first', 'last']}>{key => <>
      <input aria-label={`Edit ${key}`} />
      <button onKeyDown={event => event.preventDefault()}>{key}</button>
    </>}</List>)
    const input = screen.getByRole('textbox', { name: 'Edit first' })
    act(() => input.focus())
    expect(fireEvent.keyDown(input, { key: 'End' })).toBe(true)
    expect(document.activeElement).toBe(input)
    const button = screen.getByRole('button', { name: 'first' })
    act(() => button.focus())
    fireEvent.keyDown(button, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(button)
  })

  it('does not steal outside focus when the previously focused row disappears', () => {
    const tree = (items: string[]) => <><button>Elsewhere</button><List items={items} /></>
    const view = render(tree(keys))
    act(() => screen.getByRole('button', { name: keys[0] }).focus())
    const outside = screen.getByRole('button', { name: 'Elsewhere' })
    act(() => outside.focus())
    view.rerender(tree(keys.slice(1)))
    expect(document.activeElement).toBe(outside)
  })

  it('keeps nested list keys within the owning list', () => {
    render(<List items={['outer-first', 'outer-last']}>{key => key === 'outer-first'
      ? <List items={['inner-first', 'inner-last']} /> : <button>{key}</button>}</List>)
    const first = screen.getByRole('button', { name: 'inner-first' })
    act(() => first.focus())
    fireEvent.keyDown(first, { key: 'End' })
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'inner-last' }))
  })

  it('bounds the DOM after a deep navigation and a collection shrink', () => {
    const view = render(<List />)
    const first = screen.getByRole('button', { name: keys[0] })
    act(() => first.focus())
    fireEvent.keyDown(first, { key: 'End' })
    expect(document.activeElement).toBe(screen.getByRole('button', { name: keys[4999] }))
    expect(screen.getAllByRole('listitem').length).toBeLessThan(30)
    view.rerender(<List items={keys.slice(0, 80)} />)
    expect(screen.getAllByRole('listitem').length).toBeGreaterThan(0)
    expect(screen.getAllByRole('listitem').every(row => row.getAttribute('aria-setsize') === '80')).toBe(true)
  })
})

describe('table and collection presentation', () => {
  it('preserves table captions, alignment, native cell attributes and explicit layout overrides', () => {
    render(<Table caption="Runtime inventory" sized={false} className="custom-table" wrapClassName="custom-wrapper">
      <THead data-testid="head"><tr><Th align="right" pad={false} colSpan={2} className="custom-cell">Name</Th></tr></THead>
      <tbody><tr><Td align="center" rowSpan={2}>Ready</Td><Td>8</Td></tr></tbody>
    </Table>)
    const table = screen.getByRole('table', { name: 'Runtime inventory' })
    expect(table.classList.contains('text-[0.75rem]')).toBe(false)
    expect(table.parentElement?.classList.contains('custom-wrapper')).toBe(true)
    const header = within(table).getByRole('columnheader')
    expect(header.getAttribute('scope')).toBe('col')
    expect(header.getAttribute('colspan')).toBe('2')
    expect(header.classList.contains('text-right')).toBe(true)
    expect(header.classList.contains('px-m')).toBe(false)
    const cell = within(table).getByRole('cell', { name: 'Ready' })
    expect(cell.getAttribute('rowspan')).toBe('2')
    expect(cell.classList.contains('text-center')).toBe(true)
    expect(cell.classList.contains('px-m')).toBe(true)
  })

  it('renders backend error detail and retains retry activation', () => {
    let retries = 0
    render(<LoadError what="projects" error={new Error('Workspace unavailable')} onRetry={() => { retries++ }} />)
    expect(screen.getByRole('alert').textContent).toContain('Workspace unavailable')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retries).toBe(1)
  })

  it('keeps skeleton counts, title options, explicit sizes and announced nouns', () => {
    const list = render(<ListSkeleton rows={3} what="tasks" />)
    expect(list.container.querySelectorAll('.skeleton')).toHaveLength(9)
    expect(list.getByRole('status').textContent).toBe('Loading tasks…')
    list.unmount()
    const form = render(<FormSkeleton sections={2} rows={2} title={false} what="settings" />)
    expect(form.container.querySelectorAll('section')).toHaveLength(2)
    expect(form.container.querySelectorAll('.skeleton')).toHaveLength(14)
    form.unmount()
    const cards = render(<CardGridSkeleton cards={3} cols={3} title={false} />)
    expect(cards.container.querySelector('.grid')?.getAttribute('style')).toContain('repeat(3, minmax(0, 1fr))')
    expect(cards.container.querySelectorAll('.skeleton')).toHaveLength(12)
    cards.unmount()
    const atom = render(<Skeleton className="h-24 w-full" />).container.firstElementChild!
    expect(atom.getAttribute('aria-hidden')).toBe('true')
    expect(atom.classList.contains('h-24')).toBe(true)
  })
})
