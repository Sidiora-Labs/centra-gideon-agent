import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { TextInput, TextArea, Select, NumberField, Field, Checkbox, ChipInput } from './forms'


function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}
function expectTokens(el: HTMLElement | null, tokens: string[]) {
  const have = classOf(el)
  for (const t of tokens) expect(have, `missing "${t}" in: ${[...have].join(' ')}`).toContain(t)
}

describe('standard-field scale', () => {
  it('TextInput default render is the prior fixed chrome, byte-for-byte', () => {
    const { container } = render(<TextInput value="" onChange={() => {}} />)
    const input = container.querySelector('input')
    expectTokens(input, [
      'w-full', 'h-10', 'rounded-md', 'bg-surface-container', 'px-m',
      'text-on-surface', 'placeholder:text-on-surface-low',
      'outline-none', 'focus:ring-2', 'focus:ring-inset', 'focus:ring-primary',
    ])
    expect(input?.getAttribute('data-type')).toBe('body-m')
    expect(input?.getAttribute('type')).toBeNull()
  })

  it('TextInput size steps ride the height ladder and the on-ramp type sizes', () => {
    const sm = render(<TextInput value="" onChange={() => {}} size="sm" />).container.querySelector('input')
    const md = render(<TextInput value="" onChange={() => {}} size="md" />).container.querySelector('input')
    const lg = render(<TextInput value="" onChange={() => {}} size="lg" />).container.querySelector('input')
    expectTokens(sm, ['h-8'])
    expectTokens(md, ['h-9'])
    expectTokens(lg, ['h-10'])
    expect(sm?.getAttribute('data-type')).toBe('body-s')
    expect(md?.getAttribute('data-type')).toBe('body-s')
    expect(lg?.getAttribute('data-type')).toBe('body-m')
    for (const el of [sm, md, lg]) expect(classOf(el)).not.toContain('text-[0.875rem]')
  })

  it('TextInput surface steps swap only the fill token', () => {
    const container_ = render(<TextInput value="" onChange={() => {}} surface="container" />).container.querySelector('input')
    const high = render(<TextInput value="" onChange={() => {}} surface="high" />).container.querySelector('input')
    const base = render(<TextInput value="" onChange={() => {}} surface="base" />).container.querySelector('input')
    expect(classOf(container_)).toContain('bg-surface-container')
    expect(classOf(high)).toContain('bg-surface-high')
    expect(classOf(base)).toContain('bg-surface')
  })

  it('TextInput leadingIcon adds the canonical inset and wraps the icon', () => {
    const { container } = render(
      <TextInput value="" onChange={() => {}} leadingIcon={<svg data-testid="glyph" />} />,
    )
    const input = container.querySelector('input')
    expectTokens(input, ['pl-9', 'pr-m'])
    expect(classOf(input)).not.toContain('px-m')
    const iconSpan = container.querySelector<HTMLElement>('span.absolute')
    expectTokens(iconSpan, ['left-3', 'text-on-surface-low', 'pointer-events-none'])
    expect(iconSpan?.querySelector('[data-testid="glyph"]')).not.toBeNull()
    const plain = render(<TextInput value="" onChange={() => {}} />).container.querySelector('input')
    expect(classOf(plain)).toContain('px-m')
    expect(classOf(plain)).not.toContain('pl-9')
  })

  it('TextInput forwards provider constraints and reserves one exclusive padding branch', () => {
    const { container } = render(
      <TextInput id="requests" type="number" value="3" onChange={() => {}} min={1} max={9} minLength={2}
        maxLength={4} pattern="[0-9]+" trailingSlot={<button type="button">units</button>} />,
    )
    const input = container.querySelector('input')!
    expect(input).toMatchObject({ id: 'requests', type: 'number', min: '1', max: '9', minLength: 2, maxLength: 4, pattern: '[0-9]+' })
    expectTokens(input, ['pl-m', 'pr-10'])
    expect(classOf(input)).not.toContain('px-m')
    expect(classOf(input)).not.toContain('px-3')
  })

  it('TextInput ariaLabel survives a name (a name is not an accessible name)', () => {
    const input = render(
      <TextInput value="" onChange={() => {}} name="dep-search-x" ariaLabel="Find a prerequisite task" />,
    ).container.querySelector('input')
    expect(input?.getAttribute('aria-label')).toBe('Find a prerequisite task')
    expect(input?.getAttribute('name')).toBe('dep-search-x')
  })

  it('TextArea default render is the prior fixed chrome', () => {
    const { container } = render(<TextArea value="" onChange={() => {}} />)
    const ta = container.querySelector('textarea')
    expectTokens(ta, ['w-full', 'rounded-md', 'bg-surface-container', 'resize-y'])
    expect(ta?.getAttribute('data-type')).toBe('body-m')
  })

  it('TextArea size axis: sm/md are the dense on-ramp size, lg the page-form size', () => {
    const sm = render(<TextArea value="" onChange={() => {}} size="sm" />).container.querySelector('textarea')
    const md = render(<TextArea value="" onChange={() => {}} size="md" />).container.querySelector('textarea')
    const lg = render(<TextArea value="" onChange={() => {}} size="lg" />).container.querySelector('textarea')
    expect(sm?.getAttribute('data-type')).toBe('body-s')
    expect(md?.getAttribute('data-type')).toBe('body-s')
    expect(lg?.getAttribute('data-type')).toBe('body-m')
    for (const el of [sm, md, lg]) expect(classOf(el)).not.toContain('text-[0.875rem]')
  })

  it('TextArea mono stays byte-identical (font-mono + dense text, regardless of size)', () => {
    const monoLg = render(<TextArea value="" onChange={() => {}} mono />).container.querySelector('textarea')
    expectTokens(monoLg, ['font-mono'])
    expect(monoLg?.getAttribute('data-type')).toBe('body-s')
  })

  it('TextArea accepts an explicit id and surface', () => {
    const ta = render(<TextArea id="payload" surface="high" value="" onChange={() => {}} />).container.querySelector('textarea')!
    expect(ta.id).toBe('payload')
    expect(classOf(ta)).toContain('bg-surface-high')
    expect(classOf(ta)).not.toContain('bg-surface-container')
  })

  it('Select default render is the prior fixed chrome and carries options', () => {
    const { container } = render(
      <Select value="a" onChange={() => {}} options={[{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }]} />,
    )
    const select = container.querySelector('select')
    expectTokens(select, ['w-full', 'h-10', 'appearance-none', 'rounded-md', 'bg-surface-container'])
    expect(select?.getAttribute('data-type')).toBe('body-m')
    expect(container.querySelectorAll('option')).toHaveLength(2)
  })

  it('Select accepts an explicit id and surface', () => {
    const select = render(
      <Select id="region" surface="base" value="a" onChange={() => {}} options={[{ value: 'a', label: 'A' }]} />,
    ).container.querySelector('select')!
    expect(select.id).toBe('region')
    expect(classOf(select)).toContain('bg-surface')
    expect(classOf(select)).not.toContain('bg-surface-container')
  })
})

describe('NumberField', () => {
  it('renders the prior hand-rolled chrome, byte-for-byte (w-24 default)', () => {
    const input = render(<NumberField value={3} onChange={() => {}} />).container.querySelector('input')
    expectTokens(input, [
      'h-8', 'w-24', 'rounded-md', 'bg-surface-high', 'px-2', 'text-right',
      'text-on-surface', 'tabular-nums', 'outline-none',
      'focus:ring-2', 'focus:ring-inset', 'focus:ring-primary',
    ])
    expect(input?.getAttribute('data-type')).toBe('body-s')
    expect(input?.getAttribute('type')).toBe('number')
  })

  it('swaps only the width token when width is overridden', () => {
    const input = render(<NumberField value={3} onChange={() => {}} width="w-20" />).container.querySelector('input')
    expect(classOf(input)).toContain('w-20')
    expect(classOf(input)).not.toContain('w-24')
  })

  it('clamps to [min,max] and commits on blur, only when changed', () => {
    const onChange = vi.fn()
    const { container } = render(<NumberField value={5} min={0} max={10} onChange={onChange} />)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '42' } })
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledWith(10)
    expect(input.value).toBe('10')
  })

  it('does not commit when the clamped value is unchanged', () => {
    const onChange = vi.fn()
    const { container } = render(<NumberField value={5} min={0} max={10} onChange={onChange} />)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '5' } })
    fireEvent.blur(input)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('reverts an empty or NaN entry to the last good value without committing', () => {
    const onChange = vi.fn()
    const { container } = render(<NumberField value={7} onChange={onChange} />)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)
    expect(onChange).not.toHaveBeenCalled()
    expect(input.value).toBe('7')
  })

  it('commits on Enter (blurs the input)', () => {
    const onChange = vi.fn()
    const { container } = render(<NumberField value={1} min={0} max={100} onChange={onChange} />)
    const input = container.querySelector('input')!
    input.focus()
    fireEvent.change(input, { target: { value: '9' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith(9)
  })

  it('resyncs local state when the committed value changes externally', () => {
    const { container, rerender } = render(<NumberField value={2} onChange={() => {}} />)
    const input = container.querySelector('input')!
    expect(input.value).toBe('2')
    rerender(<NumberField value={8} onChange={() => {}} />)
    expect(input.value).toBe('8')
  })

  it('takes its accessible name from an explicit ariaLabel', () => {
    const input = render(<NumberField value={1} onChange={() => {}} ariaLabel="Retention (days)" />).container.querySelector('input')
    expect(input?.getAttribute('aria-label')).toBe('Retention (days)')
  })

  it('claims a wrapping Field label via aria-labelledby when it has no ariaLabel', () => {
    const { container } = render(
      <Field label="Warm pool size"><NumberField value={1} onChange={() => {}} /></Field>,
    )
    const input = container.querySelector('input')!
    const labelledby = input.getAttribute('aria-labelledby')
    expect(labelledby).toBeTruthy()
    expect(input.getAttribute('aria-label')).toBeNull()
    expect(container.querySelector(`[id="${labelledby}"]`)?.textContent).toBe('Warm pool size')
  })
})


describe('ChipInput accessible name', () => {
  const nameOf = (c: HTMLElement) => {
    const input = c.querySelector('input')!
    const lb = input.getAttribute('aria-labelledby')
    return {
      labelledby: lb,
      ariaLabel: input.getAttribute('aria-label'),
      announced: lb ? (c.querySelector(`[id="${lb}"]`)?.textContent ?? null) : input.getAttribute('aria-label'),
    }
  }

  it('falls back to "Add a tag" when bare and given no ariaLabel (preserved behavior)', () => {
    const r = nameOf(render(<ChipInput values={[]} onChange={() => {}} />).container)
    expect(r.labelledby).toBeNull()
    expect(r.announced).toBe('Add a tag')
  })

  it('an explicit ariaLabel replaces the hardcoded literal when there is no Field label', () => {
    const r = nameOf(render(<ChipInput values={[]} onChange={() => {}} ariaLabel="Add an alias" />).container)
    expect(r.ariaLabel).toBe('Add an alias')
    expect(r.announced).toBe('Add an alias')
  })

  it("a ui/forms Field's published label still outranks ariaLabel", () => {
    const r = nameOf(render(
      <Field label="Tags"><ChipInput values={[]} onChange={() => {}} ariaLabel="Add an alias" /></Field>,
    ).container)
    expect(r.labelledby).toBeTruthy()
    expect(r.ariaLabel).toBeNull()
    expect(r.announced).toBe('Tags')
  })

  it('names the field even once a chip exists and blanks the placeholder', () => {
    const { container } = render(
      <ChipInput values={['nickname']} onChange={() => {}} placeholder="Alias, then Enter" ariaLabel="Add an alias" />,
    )
    expect(container.querySelector('input')!.getAttribute('placeholder')).toBe('')
    expect(nameOf(container).announced).toBe('Add an alias')
  })
})


describe('Checkbox', () => {
  it('reports the next boolean', () => {
    const onChange = vi.fn()
    render(<Checkbox checked={false} onChange={onChange} ariaLabel="Select the thing" />)
    fireEvent.click(screen.getByLabelText('Select the thing'))
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('does not activate the clickable row it sits inside', () => {
    const rowClick = vi.fn()
    const onChange = vi.fn()
    render(
      <div onClick={rowClick}>
        <Checkbox checked={false} onChange={onChange} ariaLabel="Select row" />
      </div>,
    )
    fireEvent.click(screen.getByLabelText('Select row'))
    expect(onChange).toHaveBeenCalled()
    expect(rowClick).not.toHaveBeenCalled()
  })

  it('carries an accessible name', () => {
    render(<Checkbox checked onChange={() => {}} ariaLabel="Select chat about pears" />)
    const box = screen.getByLabelText('Select chat about pears') as HTMLInputElement
    expect(box.type).toBe('checkbox')
    expect(box.checked).toBe(true)
  })
})

// These operations exercise the native DOM API, including browser form semantics.
describe('native field constraints and editing guards', () => {
  it('preserves calendar bounds, keyboard hints, refs and typed blur callbacks', () => {
    const blur = vi.fn()
    let node: HTMLInputElement | null = null
    const { container } = render(<form><TextInput value="2026-10-07T09:30" onChange={() => {}}
      type="datetime-local" min="2026-10-01T00:00" max="2026-10-31T23:59" step={60}
      required name="meeting" ariaLabel="Meeting time" inputMode="numeric" autoComplete="off"
      spellCheck={false} ref={element => { node = element }} onBlur={blur} className="col-span-2" /></form>)
    const input = container.querySelector('input')!
    expect(node).toBe(input)
    expect(input.type).toBe('datetime-local')
    expect(input.min).toBe('2026-10-01T00:00')
    expect(input.max).toBe('2026-10-31T23:59')
    expect(input.step).toBe('60')
    expect(input.required).toBe(true)
    expect(input.inputMode).toBe('numeric')
    expect(input.autocomplete).toBe('off')
    expect(input.getAttribute('spellcheck')).toBe('false')
    expect(input.classList.contains('col-span-2')).toBe(true)
    expect(new FormData(container.querySelector('form')!).get('meeting')).toBe('2026-10-07T09:30')
    input.focus()
    input.blur()
    expect(blur).toHaveBeenCalledOnce()
    expect(blur.mock.calls[0][0].target).toBe(input)
  })

  it('uses genuine native email/url and required constraints', () => {
    const { container } = render(<><TextInput type="email" value="invalid" onChange={() => {}} required />
      <TextInput type="url" value="https://example.org" onChange={() => {}} /></>)
    const [email, url] = Array.from(container.querySelectorAll('input'))
    expect(email.validity.typeMismatch).toBe(true)
    expect(email.required).toBe(true)
    expect(url.validity.valid).toBe(true)
  })

  it('keeps native disabled submission semantics and binds a real explanation plus hint', () => {
    const change = vi.fn()
    const { container } = render(<form><Field label="Endpoint" hint="Use the registered endpoint">
      <TextInput name="endpoint" ariaLabel="Endpoint" value="https://example.org" onChange={change}
        disabled disabledReason="Policy prevents edits" /></Field></form>)
    const input = container.querySelector('input')!
    expect(input.disabled).toBe(true)
    expect(new FormData(container.querySelector('form')!).has('endpoint')).toBe(false)
    const descriptions = input.getAttribute('aria-describedby')!.split(' ').map(id => container.querySelector(`[id="${id}"]`)!.textContent)
    expect(descriptions).toEqual(['Use the registered endpoint', 'Policy prevents edits'])
    fireEvent.change(input, { target: { value: 'changed' } })
    expect(change).not.toHaveBeenCalled()
  })

  it('keeps an explicitly readonly text field focusable and guards changes', () => {
    const change = vi.fn()
    const { container, rerender } = render(<TextInput value="retained" onChange={change} readOnly readOnlyReason="Already bound" />)
    const input = container.querySelector('input')!
    expect(input.disabled).toBe(false)
    expect(input.readOnly).toBe(true)
    input.focus()
    expect(document.activeElement).toBe(input)
    expect(container.querySelector(`[id="${input.getAttribute('aria-describedby')}"]`)!.textContent).toBe('Already bound')
    fireEvent.change(input, { target: { value: 'changed' } })
    expect(change).not.toHaveBeenCalled()
    expect(input.value).toBe('retained')
    rerender(<TextInput value="retained" onChange={change} />)
    expect(input.getAttribute('aria-describedby')).toBeNull()
    fireEvent.change(input, { target: { value: 'edited' } })
    expect(change).toHaveBeenCalledWith('edited')
  })

  it('forwards textarea constraints, form name, ref and keyboard/blur handlers', () => {
    const blur = vi.fn(), key = vi.fn(), change = vi.fn()
    let node: HTMLTextAreaElement | null = null
    const { container } = render(<TextArea name="passage" ariaLabel="Passage" value="saved" onChange={change}
      required minLength={2} maxLength={100} spellCheck={false} wrap="hard" readOnly readOnlyReason="Saved excerpt"
      ref={element => { node = element }} onBlur={blur} onKeyDown={key} className="min-h-48" />)
    const textarea = container.querySelector('textarea')!
    expect(node).toBe(textarea)
    expect(textarea.name).toBe('passage')
    expect(textarea.required).toBe(true)
    expect(textarea.minLength).toBe(2)
    expect(textarea.maxLength).toBe(100)
    expect(textarea.wrap).toBe('hard')
    expect(textarea.getAttribute('spellcheck')).toBe('false')
    textarea.focus()
    expect(document.activeElement).toBe(textarea)
    fireEvent.keyDown(textarea, { key: 'Escape' })
    fireEvent.change(textarea, { target: { value: 'changed' } })
    textarea.blur()
    expect(key).toHaveBeenCalledOnce()
    expect(blur).toHaveBeenCalledOnce()
    expect(change).not.toHaveBeenCalled()
    expect(textarea.value).toBe('saved')
  })

  it('guards a focusable readonly select against pointer, keyboard and synthetic selection', () => {
    const change = vi.fn(), blur = vi.fn()
    let node: HTMLSelectElement | null = null
    const { container, rerender } = render(<Select value="a" onChange={change} required name="region" ariaLabel="Region"
      options={[{ value: 'a', label: 'Here' }, { value: 'b', label: 'Other' }, { value: 'c', label: 'Unavailable', disabled: true }]}
      readOnly readOnlyReason="Fixed deployment region" ref={element => { node = element }} onBlur={blur} />)
    const select = container.querySelector('select')!
    expect(node).toBe(select)
    expect(select.required).toBe(true)
    expect(select.disabled).toBe(false)
    expect(select.getAttribute('aria-readonly')).toBe('true')
    select.focus()
    expect(document.activeElement).toBe(select)
    expect(fireEvent.mouseDown(select)).toBe(false)
    expect(fireEvent.keyDown(select, { key: 'ArrowDown' })).toBe(false)
    expect(fireEvent.keyDown(select, { key: 'b' })).toBe(false)
    expect(fireEvent.keyDown(select, { key: 'Tab' })).toBe(true)
    fireEvent.change(select, { target: { value: 'b' } })
    expect(select.value).toBe('a')
    expect(change).not.toHaveBeenCalled()
    select.blur()
    expect(blur).toHaveBeenCalledOnce()
    rerender(<Select value="a" onChange={change} options={[{ value: 'a', label: 'Here' }, { value: 'c', label: 'Unavailable', disabled: true }]} />)
    fireEvent.change(select, { target: { value: 'c' } })
    expect(change).not.toHaveBeenCalled()
    expect(select.value).toBe('a')
  })

  it('keeps checkbox readonly focus, isolation, submission and native disabled contracts', () => {
    const change = vi.fn(), row = vi.fn(), blur = vi.fn()
    let node: HTMLInputElement | null = null
    const { container, rerender } = render(<form><div onClick={row}><Checkbox checked onChange={change}
      ariaLabel="Select passage" id="passage-check" name="passage" value="saved" required readOnly readOnlyReason="Selection is locked"
      ref={element => { node = element }} onBlur={blur} /></div></form>)
    const checkbox = container.querySelector('input')!
    expect(node).toBe(checkbox)
    expect(checkbox.required).toBe(true)
    checkbox.focus()
    expect(document.activeElement).toBe(checkbox)
    fireEvent.click(checkbox)
    expect(checkbox.checked).toBe(true)
    expect(change).not.toHaveBeenCalled()
    expect(row).not.toHaveBeenCalled()
    expect(new FormData(container.querySelector('form')!).get('passage')).toBe('saved')
    checkbox.blur()
    expect(blur).toHaveBeenCalledOnce()
    rerender(<form><Checkbox checked onChange={change} ariaLabel="Select passage" name="passage"
      disabled disabledReason="Unavailable selection" /></form>)
    expect(container.querySelector('input')!.disabled).toBe(true)
    expect(new FormData(container.querySelector('form')!).has('passage')).toBe(false)
  })
})
