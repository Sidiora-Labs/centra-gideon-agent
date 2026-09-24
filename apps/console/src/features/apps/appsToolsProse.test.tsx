import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { AppCard, AppConfigDialog, AppDetailPanel, StoreDetailPanel, StoreView, type StoreItem } from './AppsSection'
import { ToolInspector, ToolRunResult } from '../tools/ToolInspector'
import { GroupBlock } from '../tools/ToolsPage'
import { Markdown } from '../../shared/ui/Markdown'
import { SidePanel } from '../../shared/ui/SidePanel'
import { Select } from '../../shared/ui/forms'
import type { AppSummary, ToolItem } from '../../shared/data/api'

const prose = '**Important** `argument` [Guide](https://example.com/guide)'
const done = () => {}
const item: StoreItem = {
  name: 'internal-id', displayName: 'Workspace Helper', description: prose,
  version: '1', icon: 'Blocks', author: '', source: '', sourceKind: 'bundled',
  isProvider: false, providerType: '', tags: ['one', 'two', 'three', 'four'],
  installed: false, enabled: false, hasUI: false,
}
const app: AppSummary = {
  ...item, enabled: true, hasBackend: false, hasConfig: true, origin: 'builtin',
  uiPages: [], permissions: {}, backendRunning: false, backendPort: null,
}
const tool: ToolItem = { name: 'inspect', provider: 'native', description: prose, disabled: false, providerDisabled: false,
  parameters: { type: 'object', properties: { path: { type: 'string', description: prose } } } }

function expectProse(container: HTMLElement, count = 1) {
  expect(container.querySelectorAll('strong')).toHaveLength(count)
  expect(container.querySelectorAll('code')).toHaveLength(count)
  expect(container.querySelectorAll('a[href="https://example.com/guide"]')).toHaveLength(count)
  expect(container.textContent).not.toContain('**Important**')
}

describe('Apps and Tools prose', () => {
  it('renders app-card prose and declares truncated tag counts', () => {
    const { container } = render(<AppCard item={item} index={0} busy={false} onInstall={done} onOpen={done} onAction={done} />)
    expectProse(container)
    expect(screen.getByText('Showing 3 of 4 tags')).toBeInTheDocument()
    expect(screen.queryByText('four')).not.toBeInTheDocument()
  })
  it('renders installed app detail prose', () => {
    const { container } = render(<AppDetailPanel app={app} onClose={done} onChanged={done} onOpen={done} />)
    expectProse(container)
  })
  it('renders Store detail prose', () => {
    const { container } = render(<StoreDetailPanel item={item} onInstalled={done} />)
    expectProse(container)
  })
  it('renders both tool descriptions and parameter descriptions', () => {
    const { container } = render(<ToolInspector tool={tool} />)
    expectProse(container, 2)
  })
  it('renders tool-row prose without nesting links in buttons', () => {
    const { container } = render(<GroupBlock g={{ key: 'native', label: 'Native', kind: 'native', tools: [tool] }}
      onOpen={done} onToggleServer={done} onRemoveServer={done} onToggleTool={done} onToggleProvider={done} onReconnect={done} reconnecting={null} />)
    expectProse(container)
    expect(container.querySelector('button a')).toBeNull()
    expect(screen.getByRole('button', { name: 'inspect' })).toBeInTheDocument()
  })
  it('keeps inline output phrasing-only and rejects unsafe URLs and active HTML', () => {
    const { container } = render(<p><Markdown inline>{'# Heading\n\n**bold** `code` [bad](javascript:alert) <iframe src="https://example.com"></iframe>'}</Markdown></p>)
    expect(container.querySelector('p p, h1, pre, iframe, script')).toBeNull()
    expect(container.querySelector('strong')).toHaveTextContent('bold')
    expect(container.querySelector('[href^="javascript:"]')).toBeNull()
  })
  it('does not expose executable raw HTML in prose', () => {
    const { container } = render(<Markdown>{'<iframe src="https://example.com"></iframe><script>alert(1)</script><form>unsafe</form>'}</Markdown>)
    expect(container.querySelector('iframe, script, form')).toBeNull()
  })
})

describe('Apps and Tools controls', () => {
  it('preserves one mounted body and an edited field across expansion', () => {
    function Editor() {
      const [value, setValue] = useState('initial')
      return <input aria-label="Draft" value={value} onChange={(e) => setValue(e.target.value)} />
    }
    render(<SidePanel title="Tool configuration" onClose={done}><Editor /></SidePanel>)
    const input = screen.getByRole('textbox', { name: 'Draft' })
    fireEvent.change(input, { target: { value: 'edited' } })
    fireEvent.click(screen.getByRole('button', { name: 'Expand to full width' }))
    expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(input)
    expect(input).toHaveValue('edited')
    fireEvent.click(screen.getByRole('button', { name: 'Collapse to panel' }))
    expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(input)
    expect(input).toHaveValue('edited')
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
  })
  it('titles configuration with the app display name', () => {
    render(<AppConfigDialog displayName={app.displayName} onClose={done}>Configuration</AppConfigDialog>)
    expect(screen.getByRole('dialog', { name: 'Configure Workspace Helper' })).toBeInTheDocument()
    const source = readFileSync('src/features/apps/AppsSection.tsx', 'utf8')
    expect(source).toContain('displayName={app.displayName}')
    expect(source).toContain('displayName={configFor.displayName}')
  })
  it('draws a non-intercepting chevron and keeps Select usable', () => {
    let selected = ''
    const { container } = render(<Select ariaLabel="Format" value="text" onChange={(v) => { selected = v }} options={[{ value: 'text', label: 'Text' }, { value: 'json', label: 'JSON' }]} />)
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(container.querySelector('svg')).toHaveClass('pointer-events-none')
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'json' } })
    expect(selected).toBe('json')
  })
  it('exposes successful and failed tool results as keyboard-accessible scroll regions', () => {
    const { rerender } = render(<ToolRunResult result={{ ok: true, output: 'long result\n'.repeat(100) }} />)
    expect(screen.getByRole('region', { name: 'Tool result' })).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('region', { name: 'Tool result' })).toHaveClass('max-h-96', 'overflow-auto')
    rerender(<ToolRunResult result={{ ok: false, error: 'Failure details' }} />)
    expect(screen.getByRole('region', { name: 'Tool result' })).toHaveTextContent('Failure details')
  })
  it('announces initial Store indexing instead of an empty result', () => {
    render(<StoreView catalog={undefined} result={[]} totalKnown={0} installedCount={0} onInstalled={done} reloadCatalog={done} onClearFilters={done} filtersActive={false} onOpen={done} onAction={done} onOpenSources={done} />)
    expect(screen.getByText(/Indexing the Store/)).toHaveAttribute('role', 'status')
    expect(screen.queryByText(/No apps found/)).toBeNull()
  })
  it('keeps refresh indexing visible and avoids a premature empty notice', () => {
    render(<StoreView indexing catalog={{ bundled: [], gitSources: [] }} result={[]} totalKnown={0} installedCount={0} onInstalled={done} reloadCatalog={done} onClearFilters={done} filtersActive={false} onOpen={done} onAction={done} onOpenSources={done} />)
    expect(screen.getByRole('status')).toHaveTextContent('Indexing the Store')
    expect(screen.queryByText(/No apps found/)).toBeNull()
  })
  it('separates the view selector at widths below 640px, including 390px', () => {
    const source = readFileSync('src/features/apps/AppsSection.tsx', 'utf8')
    expect(source).toContain('className="hidden sm:block">{viewSelector}')
    expect(source).toContain('className="px-l py-s sm:hidden" data-apps-mobile-view>{viewSelector}')
    expect(source).toContain('minmax(min(300px, 100%), 1fr)')
  })
})
