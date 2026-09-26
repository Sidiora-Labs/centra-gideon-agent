import { useState } from 'react'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SidePanel } from './SidePanel'

let originalWidth: PropertyDescriptor | undefined
let originalMatchMedia: PropertyDescriptor | undefined
let originalBootstrap: unknown
let mobile: { matches: boolean; listeners: Set<() => void> }

function viewport(width: number) {
  act(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
    const matches = width <= 768
    if (matches !== mobile.matches) {
      mobile.matches = matches
      for (const listener of mobile.listeners) listener()
    }
    window.dispatchEvent(new Event('resize'))
  })
}

function panelColumn(region: HTMLElement) {
  const column = region.querySelector(':scope > div.flex.h-full.flex-col')
  expect(column).toBeInstanceOf(HTMLElement)
  return column as HTMLElement
}

function PanelHost({ onExpand = () => {} }: { onExpand?: () => void }) {
  const [open, setOpen] = useState(false)
  const [expandedPage, setExpandedPage] = useState(false)
  return <div className="gideon-shell">
    <button onClick={() => setOpen(true)}>Open details</button>
    <main>Workspace remains available</main>
    {open && <SidePanel title="A very long saved workflow title with several meaningful words" fillHeight
      storeKey="responsive-test-panel-w" onClose={() => setOpen(false)}
      onExpand={expandedPage ? onExpand : undefined}>
      <input aria-label="Panel search" defaultValue="keep this value" />
      <p>Last line of the panel body stays mounted while its container changes.</p>
      <button onClick={() => setExpandedPage(true)}>Use dedicated page</button>
    </SidePanel>}
  </div>
}

beforeEach(() => {
  originalWidth = Object.getOwnPropertyDescriptor(window, 'innerWidth')
  originalMatchMedia = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  originalBootstrap = Reflect.get(globalThis, '__GIDEON_CLOUD_CONFIG__')
  Reflect.set(globalThis, '__GIDEON_CLOUD_CONFIG__', { hosted: true })
  mobile = { matches: false, listeners: new Set() }
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: (query: string) => query === '(max-width: 768px)'
    ? { media: query, get matches() { return mobile.matches }, addEventListener: (_: string, listener: () => void) => mobile.listeners.add(listener),
      removeEventListener: (_: string, listener: () => void) => mobile.listeners.delete(listener) }
    : { media: query, matches: true, addEventListener: () => {}, removeEventListener: () => {} } })
  viewport(769)
  localStorage.removeItem('responsive-test-panel-w')
  document.documentElement.dir = 'ltr'
})

afterEach(() => {
  vi.restoreAllMocks()
  if (originalWidth) Object.defineProperty(window, 'innerWidth', originalWidth)
  if (originalMatchMedia) Object.defineProperty(window, 'matchMedia', originalMatchMedia)
  Reflect.set(globalThis, '__GIDEON_CLOUD_CONFIG__', originalBootstrap)
  localStorage.removeItem('responsive-test-panel-w')
  document.documentElement.dir = 'ltr'
})

describe('responsive SidePanel', () => {
  it('reserves half a 769px viewport, resizes at larger widths, and moves the same body into a mobile sheet', () => {
    const host = render(<PanelHost />)
    const trigger = screen.getByRole('button', { name: 'Open details' })
    trigger.focus()
    fireEvent.click(trigger)
    let region = screen.getByRole('region')
    let separator = within(region).getByRole('separator')
    expect(separator).toHaveAttribute('aria-valuemin', '320')
    expect(separator).toHaveAttribute('aria-valuemax', '384')
    expect(separator).toHaveAttribute('aria-valuenow', '384')
    expect(panelColumn(region).style.width).toBe('384px')
    expect(screen.getByText('Workspace remains available')).toBeInTheDocument()

    viewport(1440)
    region = screen.getByRole('region')
    separator = within(region).getByRole('separator')
    fireEvent.keyDown(separator, { key: 'End' })
    expect(separator).toHaveAttribute('aria-valuemax', '720')
    expect(panelColumn(region).style.width).toBe('720px')
    viewport(900)
    expect(panelColumn(region).style.width).toBe('450px')
    expect(separator).toHaveAttribute('aria-valuenow', '450')
    expect(separator).toHaveAttribute('aria-valuemax', '450')

    viewport(768)
    expect(screen.queryByRole('region')).toBeNull()
    const dialog = screen.getByRole('dialog', { name: /A very long saved workflow title/ })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toContainElement(screen.getByRole('textbox', { name: 'Panel search' }))
    expect(screen.getByRole('textbox', { name: 'Panel search' })).toHaveValue('keep this value')
    expect(screen.getByText('Last line of the panel body stays mounted while its container changes.')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(trigger).toHaveFocus()
    host.unmount()
  })

  it('keeps a dedicated-page action reachable in the mobile header', () => {
    const onExpand = vi.fn()
    viewport(390)
    render(<PanelHost onExpand={onExpand} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open details' }))
    fireEvent.click(screen.getByRole('button', { name: 'Use dedicated page' }))
    const dialog = screen.getByRole('dialog')
    const action = within(dialog).getByRole('button', { name: 'Open full page' })
    fireEvent.click(action)
    expect(onExpand).toHaveBeenCalledOnce()
    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })

  it('expands and collapses without remounting editable body content', () => {
    viewport(900)
    const onClose = vi.fn()
    render(<SidePanel title="Inspector" onClose={onClose}>
      <input aria-label="Inspector note" defaultValue="preserve me" />
    </SidePanel>)
    const input = screen.getByRole('textbox', { name: 'Inspector note' })
    fireEvent.change(input, { target: { value: 'edited while docked' } })
    fireEvent.click(screen.getByRole('button', { name: 'Expand to full width' }))
    expect(screen.getByRole('region', { name: 'Inspector' })).toHaveClass('fixed')
    expect(screen.getByRole('textbox', { name: 'Inspector note' })).toBe(input)
    expect(input).toHaveValue('edited while docked')
    fireEvent.click(screen.getByRole('button', { name: 'Collapse to panel' }))
    expect(screen.getByRole('region', { name: 'Inspector' })).not.toHaveClass('fixed')
    expect(screen.getByRole('textbox', { name: 'Inspector note' })).toBe(input)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('keeps left-edge resize direction in an RTL document while retaining the half-width cap', () => {
    document.documentElement.dir = 'rtl'
    viewport(900)
    render(<SidePanel title="تفاصيل المهمة" onClose={() => {}}>Body</SidePanel>)
    const region = screen.getByRole('region')
    const separator = within(region).getByRole('separator')
    expect(document.documentElement.dir).toBe('rtl')
    expect(separator).toHaveAttribute('aria-valuenow', '420')
    fireEvent.keyDown(separator, { key: 'ArrowRight' })
    expect(separator).toHaveAttribute('aria-valuenow', '436')
    fireEvent.keyDown(separator, { key: 'ArrowLeft' })
    expect(separator).toHaveAttribute('aria-valuenow', '420')
    expect(panelColumn(region).style.width).toBe('420px')
    viewport(769)
    expect(panelColumn(region).style.width).toBe('384px')
    fireEvent.click(screen.getByRole('button', { name: 'Expand to full width' }))
    expect(screen.getByRole('region')).toHaveClass('fixed')
    fireEvent.click(screen.getByRole('button', { name: 'Collapse to panel' }))
  })
})
