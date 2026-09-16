import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ChecklistEditor } from './formControls'


type Item = { description: string; completed: boolean }

const items: Item[] = [
  { description: 'first', completed: false },
  { description: 'second', completed: true },
]

function setup(over: { ordered?: boolean } = {}) {
  const onChange = vi.fn()
  render(
    <ChecklistEditor
      items={items}
      onChange={onChange}
      doneKey="completed"
      placeholder="Add a step"
      ordered={over.ordered ?? true}
    />,
  )
  return { onChange }
}

describe('two-stage destructive reveal', () => {
  it('does NOT remove on the first click', async () => {
    const { onChange } = setup()
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    expect(onChange).not.toHaveBeenCalled()
  })

  it('reveals a confirm affordance instead', async () => {
    setup()
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    expect(screen.getByText('Remove?')).toBeTruthy()
  })

  it('removes on the SECOND click, and only that row', async () => {
    const { onChange } = setup()
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    await userEvent.click(screen.getByText('Remove?'))
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toEqual([{ description: 'second', completed: true }])
  })

  it('arms only ONE row at a time', async () => {
    setup()
    const buttons = screen.getAllByLabelText('Remove')
    await userEvent.click(buttons[0])
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    expect(screen.getAllByText('Remove?')).toHaveLength(1)
  })
})

describe('checked-locks-drag', () => {
  it('keeps an UNCHECKED row draggable', () => {
    setup()
    expect(screen.getByText('first')).toBeTruthy()
  })

  it('renders a completed row OUTSIDE the reorder group', () => {
    setup()
    const done = screen.getByText('second').closest('div')
    expect(done).toBeTruthy()
    expect(screen.getByTitle('A completed step keeps its place')).toBeTruthy()
  })

  it('does not advertise a lock on an incomplete row', () => {
    setup()
    expect(screen.queryAllByTitle('A completed step keeps its place')).toHaveLength(1)
  })

  it('still toggles a completed row', () => {
    setup()
    expect(screen.getByText('second')).toBeTruthy()
  })
})

describe('the unordered path is unchanged', () => {
  it('offers no grip when order is not meaningful', () => {
    setup({ ordered: false })
    expect(screen.queryAllByTitle('A completed step keeps its place')).toHaveLength(0)
  })

  it('still uses the two-stage remove', async () => {
    const onChange = vi.fn()
    render(
      <ChecklistEditor
        items={items}
        onChange={onChange}
        doneKey="completed"
        placeholder="Add a criterion"
      />,
    )
    await userEvent.click(screen.getAllByLabelText('Remove')[0])
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText('Remove?')).toBeTruthy()
  })
})
