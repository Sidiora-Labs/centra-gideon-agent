import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { List, LayoutGrid, Columns3, GitFork } from 'lucide-react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Segmented } from './Segmented'


const OPTS = [
  { key: 'list', label: 'List view', icon: List },
  { key: 'cards', label: 'Cards view', icon: LayoutGrid },
  { key: 'board', label: 'Kanban board', icon: Columns3 },
  { key: 'dag', label: 'Dependency graph', icon: GitFork },
]

describe('HeaderSegmented reaches the collapsed rung', () => {
  it('passes collapse="menu" to Segmented', () => {
    const src = readFileSync(join(process.cwd(), "src/shared/ui/HeaderActions.tsx"), 'utf8')
    const m = src.match(/return <Segmented[^/]*\/>/)
    expect(m, 'HeaderSegmented should render exactly one <Segmented/>').toBeTruthy()
    expect(
      m![0],
      'Without collapse="menu" a header Segmented has no rung below icon-only, so its ' +
        'surplus segments get clipped by the rail cap instead of collapsing to a pill.',
    ).toMatch(/collapse="menu"/)
  })
})

describe('the collapsed Segmented pill', () => {
  function renderCollapsed(iconOnly: boolean) {
    vi.stubGlobal('ResizeObserver', class {
      observe() {} unobserve() {} disconnect() {}
    })
    const spy = vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(400)
    const r = render(
      <div style={{ width: 40 }}>
        <Segmented options={OPTS} value="list" onChange={() => {}} ariaLabel="View"
          collapse="menu" iconOnly={iconOnly} />
      </div>,
    )
    return { ...r, restore: () => { spy.mockRestore(); vi.unstubAllGlobals() } }
  }

  it('drops its label and chevron when the cluster is already icon-only', () => {
    const { container, restore } = renderCollapsed(true)
    try {
      const trigger = [...container.querySelectorAll('button[aria-expanded]')]
        .find((b) => !b.closest('[aria-hidden="true"]'))
      expect(trigger, 'collapsed pill should render a trigger').toBeTruthy()
      expect(trigger!.textContent?.trim(), 'icon-only pill must render no text').toBe('')
      expect(trigger!.getAttribute('aria-label')).toBe('View')
      expect(trigger!.className).toMatch(/size-8|size-6/)
    } finally { restore() }
  })

  it('keeps its label when the cluster is NOT icon-only', () => {
    const { container, restore } = renderCollapsed(false)
    try {
      const trigger = [...container.querySelectorAll('button[aria-expanded]')]
        .find((b) => !b.closest('[aria-hidden="true"]'))
      expect(trigger!.textContent).toContain('List view')
      expect(trigger!.className).not.toMatch(/size-8 |size-6 /)
    } finally { restore() }
  })

  it('still offers every option, with full labels, once opened', () => {
    const { container, restore } = renderCollapsed(true)
    try {
      const trigger = [...container.querySelectorAll('button[aria-expanded]')]
        .find((b) => !b.closest('[aria-hidden="true"]'))!
      fireEvent.click(trigger)
      for (const o of OPTS) expect(screen.getByText(o.label)).toBeInTheDocument()
    } finally { restore() }
  })
})


describe('Segmented tabs do not shrink', () => {
  const src = readFileSync(join(process.cwd(), "src/shared/ui/Segmented.tsx"), 'utf8')

  it('the tab button declares shrink-0', () => {
    const tab = src.slice(src.indexOf('role="tab"')).split('</motion.button>')[0]
    expect(tab, 'a tab crushed to 15px is neither legible nor tappable').toMatch(/inline-flex shrink-0 items-center/)
  })

  it('renders it on every option, both densities', () => {
    for (const size of ['md', 'sm'] as const) {
      const { container, unmount } = render(
        <Segmented ariaLabel="t" size={size} value="a" onChange={() => {}}
          options={[{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }]} />,
      )
      const tabs = [...container.querySelectorAll('[role="tab"]')]
      expect(tabs.length).toBe(2)
      for (const t of tabs) expect(t.className, `size=${size}`).toMatch(/\bshrink-0\b/)
      unmount()
    }
  })
})
