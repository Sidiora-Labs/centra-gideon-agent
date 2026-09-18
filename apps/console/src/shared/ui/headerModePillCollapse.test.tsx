// The mode selector now leaves the rail instead of being squeezed inside it. What it does NOT
// fix: at extreme widths the header rail can still overlap the page title, because the rail cap
// that prevents that is computed from measured widths the mode pill no longer contributes to.
// That overlap is a separate issue against HeaderActions' cap arithmetic and is deliberately
// not addressed here — this file asserts the collapse, not the overlap.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, screen, within } from '@testing-library/react'
import { Bot, ClipboardList, Hammer, MessageSquare } from 'lucide-react'
import { HeaderActions, HeaderControl, HeaderModePill } from './HeaderActions'


const MODES = [
  { key: 'agent', label: 'Agent', icon: Bot, title: 'Agent — full execution' },
  { key: 'ask', label: 'Ask', icon: MessageSquare, title: 'Ask — read-only Q&A' },
  { key: 'plan', label: 'Plan', icon: ClipboardList, title: 'Plan — no tools' },
  { key: 'build', label: 'Build', icon: Hammer, title: 'Build — one artifact' },
]
const MIN_TARGET = 40

function mount(onChange: (k: string) => void = () => {}) {
  return render(
    <header className="flex">
      <div data-header-left />
      <HeaderActions>
        <HeaderModePill ariaLabel="Task mode" value="plan" options={MODES} onChange={onChange} />
        <HeaderControl label="New chat" priority="primary" icon={Bot} onClick={() => {}} />
      </HeaderActions>
    </header>,
  )
}

function squeeze(headerWidth: number) {
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockImplementation(function (this: HTMLElement) {
    if (!this.className.includes('opacity-0')) return 0
    return this.textContent?.trim() ? 400 : 84
  })
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
    return this.tagName === 'HEADER' ? headerWidth : 0
  })
}

const rail = (container: HTMLElement) => {
  const el = container.querySelector<HTMLElement>('div.overflow-x-auto')
  if (!el) throw new Error('the header rail moved — this file measures nothing')
  return el
}
const liveTrigger = (container: HTMLElement, name: RegExp) =>
  [...container.querySelectorAll<HTMLElement>('button')]
    .filter((b) => !b.closest('[aria-hidden="true"]'))
    .find((b) => name.test(b.getAttribute('aria-label') ?? ''))

afterEach(() => { vi.restoreAllMocks() })

describe('the mode selector at a normal width', () => {
  it('renders the full pill, label and all, and caps nothing', () => {
    const { container } = mount()
    const trigger = liveTrigger(container, /Task mode/)
    expect(trigger, 'the pill must be in the rail at full width').toBeTruthy()
    expect(trigger!.textContent).toContain('Plan')
    expect(trigger!.getAttribute('aria-haspopup')).toBe('menu')
    expect(rail(container).style.maxWidth, 'nothing is squeezed at full width').toBe('')
    expect(liveTrigger(container, /More actions/), 'nothing overflowed').toBeUndefined()
  })
})

describe('the mode selector at a width that fits icons only', () => {
  it('keeps the pill in the rail at its full 40px target, without its label', () => {
    squeeze(200)
    const { container } = mount()
    const trigger = liveTrigger(container, /Task mode/)
    expect(trigger, 'an icon-sized pill still fits — it must not vanish').toBeTruthy()
    expect(trigger!.textContent?.trim(), 'the compact rung drops the label').toBe('')
    expect(trigger!.className, `a mode control may never go under ${MIN_TARGET}px`).toContain('size-10')
  })
})

describe('the mode selector at a width that fits nothing', () => {
  it('collapses into the compact menu instead of shrinking into a strip', () => {
    squeeze(0)
    const { container } = mount()
    expect(liveTrigger(container, /Task mode/), 'the pill must leave the rail, not shrink inside it').toBeUndefined()
    const more = liveTrigger(container, /More actions/)
    expect(more, 'the compact menu must take it over').toBeTruthy()
    expect(more!.className, `the surviving control keeps the ${MIN_TARGET}px target`).toContain('size-10')

    const cap = rail(container).style.maxWidth
    const width = cap ? Number.parseFloat(cap) : Number.POSITIVE_INFINITY
    if (width < MIN_TARGET) {
      expect(
        rail(container).querySelectorAll('button').length,
        'a control left inside a rail narrower than a tap target IS the unusable strip',
      ).toBe(0)
    }
  })

  it('offers every mode from that menu, with the current one marked', () => {
    squeeze(0)
    const { container } = mount()
    fireEvent.click(liveTrigger(container, /More actions/)!)
    const group = screen.getByRole('group', { name: 'Task mode' })
    const rows = within(group).getAllByRole('menuitemradio')
    expect(rows).toHaveLength(MODES.length)
    for (const [i, mode] of MODES.entries()) {
      expect(within(rows[i]).getByText(mode.label), `${mode.key} must be reachable`).toBeInTheDocument()
      expect(within(rows[i]).getByText(mode.title), 'the pill\'s tooltip survives the collapse').toBeInTheDocument()
    }
    for (const row of rows) {
      const checked = row.getAttribute('aria-checked') === 'true'
      expect(checked).toBe(row.textContent!.includes('Plan'))
    }
  })

  it('selects a mode from that menu — the rows are live, not decorative', () => {
    squeeze(0)
    const picked: string[] = []
    const { container } = mount((k) => picked.push(k))
    fireEvent.click(liveTrigger(container, /More actions/)!)
    fireEvent.click(within(screen.getByRole('group', { name: 'Task mode' })).getByText('Build'))
    expect(picked).toEqual(['build'])
  })

  it('still shows the ordinary actions as plain rows', () => {
    squeeze(0)
    const { container } = mount()
    fireEvent.click(liveTrigger(container, /More actions/)!)
    const live = screen.getAllByText('New chat').filter((el) => !el.closest('[aria-hidden="true"]'))
    expect(live, 'the plain action keeps exactly one live row').toHaveLength(1)
    expect(live[0].closest('button'), 'and it is still clickable').toBeTruthy()
  })
})
