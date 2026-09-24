import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ts from 'typescript'
import { InboxConfigBoundary } from './InboxConfigBoundary'
import { Toggle } from '../../shared/ui/Toggle'

function controls() {
  return <>
    <Toggle on={false} onChange={() => {}} label="Poll message sources" />
    <Toggle on={false} onChange={() => {}} label="Engagement ranking" />
  </>
}

describe('inbox configuration read presentation', () => {
  it('announces failure and retries without presenting unknown values as OFF', () => {
    let retried = 0
    const onRetry = () => { retried++ }
    const view = render(<InboxConfigBoundary cfgErr="Configuration unavailable" loading={false} onRetry={onRetry}>{controls()}</InboxConfigBoundary>)
    expect(screen.getByRole('alert').textContent).toContain('Configuration unavailable')
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retried).toBe(1)
    view.rerender(<InboxConfigBoundary cfgErr="" loading onRetry={onRetry}>{controls()}</InboxConfigBoundary>)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('status').getAttribute('aria-busy')).toBe('true')
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
    view.rerender(<InboxConfigBoundary cfgErr="" loading={false} onRetry={onRetry}>{controls()}</InboxConfigBoundary>)
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getAllByRole('switch')).toHaveLength(2)
    for (const control of screen.getAllByRole('switch')) {
      expect(control.getAttribute('aria-checked')).toBe('false')
      expect(control.hasAttribute('disabled')).toBe(false)
    }
  })

  it('shows a fresh read as loading rather than OFF', () => {
    render(<InboxConfigBoundary cfgErr="" loading onRetry={() => {}}>{controls()}</InboxConfigBoundary>)
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
    expect(screen.getByRole('status').textContent).toContain('inbox configuration')
  })
})

const source = (path: string) => readFileSync(join(process.cwd(), 'src/features', path), 'utf8')

function boundaries(text: string) {
  const file = ts.createSourceFile('panel.tsx', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const found: string[] = []
  const visit = (node: ts.Node) => {
    if (ts.isJsxElement(node) && node.openingElement.tagName.getText(file) === 'InboxConfigBoundary') found.push(node.getText(file))
    ts.forEachChild(node, visit)
  }
  visit(file)
  return found
}

describe('both production panel variants wire the configuration boundary', () => {
  it('drawer protects its collection switches with the read state and retry', () => {
    const panel = source('inbox/InboxSettingsPanel.tsx')
    const regions = boundaries(panel)
    expect(regions).toHaveLength(1)
    expect(regions[0]).toContain('switches.map')
    expect(regions[0]).toContain('cfgErr={cfgErr}')
    expect(regions[0]).toContain('loading={cfgLoading}')
    expect(regions[0]).toContain('onRetry={retryConfig}')
    const hook = source('inbox/inboxSettingsState.ts')
    expect(hook).toContain('const [cfgErr, setCfgErr]')
    expect(hook).toContain('.catch(error => { if (current) setCfgErr(')
    expect(hook).not.toContain('flags.reset({ engagement: false, sources: false })')
    expect(hook).toContain("setCfgErr(''); setCfgLoading(true); setCfgRevision(value => value + 1)")
    expect(hook).toContain('}, [cfgRevision])')
    expect(hook).toContain('.finally(() => { if (current) setCfgLoading(false) })')
  })

  it('settings protects collection and triage while keeping retention independent', () => {
    const panel = source('settings/InboxSettingsPanel.tsx')
    const regions = boundaries(panel)
    expect(regions).toHaveLength(2)
    expect(regions[0]).toContain('label="Poll message sources"')
    expect(regions[0]).toContain('label="Engagement ranking"')
    expect(regions[1]).toContain('label="Morning triage digest"')
    for (const region of regions) {
      expect(region).toContain('cfgErr={cfgErr}')
      expect(region).toContain('loading={cfgLoading ||')
      expect(region).toContain('onRetry={refreshConfig}')
      expect(region).not.toContain('label="Auto-cleanup"')
    }
    expect(panel.indexOf('else if (configError)')).toBeLessThan(panel.indexOf('else if (config)'))
    expect(panel).toContain('revalidating: cfgLoading')
  })
})
