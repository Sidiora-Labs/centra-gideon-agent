import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from '../../Markdown'
import { registerBuiltinContentTypes } from '../../content/registerBuiltins'
import { donorStructures } from './donorStructures'
import { resolveUISpecContent } from './UISpecEmbed'
import { boundRecord } from '../qualification/generativeLiveData'
import type { GenerativeTemplate } from './uispec'

function widget(template: GenerativeTemplate) {
  const { recordId, bindings } = boundRecord(template)
  return `<widget kind="uispec" title="Real result">\n${JSON.stringify({ schemaVersion: 1, template, recordId, bindings })}\n</widget>`
}

describe('source-derived structured UISpec content path', () => {
  it('keeps the vetted server catalog synchronized with all 22 frontend donor structures', () => {
    const server = JSON.parse(readFileSync(resolve(process.cwd(), '../../runtime/gideon/workspace/uispec_catalog.json'), 'utf8'))
    expect(server).toHaveLength(22)
    for (const [index, definition] of donorStructures.entries()) {
      const spec = resolveUISpecContent(widget(definition.slug as GenerativeTemplate).split('\n')[1])
      expect(spec?.template).toBe(definition.slug)
      expect(server[index].template).toBe(definition.slug)
      expect(server[index].title).toBe(definition.title)
      const found: Record<string, string> = {}
      const scan = (value: unknown): void => {
        if (Array.isArray(value)) { value.forEach(scan); return }
        if (!value || typeof value !== 'object') return
        const node = value as Record<string, unknown>
        if (typeof node.$bind === 'string') { found[node.$bind] = String(node.$kind); return }
        Object.values(node).forEach(scan)
      }
      scan(definition.tree)
      expect(server[index].bindings).toEqual(found)
    }
  })

  it('mounts actual chart bindings through the existing Markdown widget parser and content registry', () => {
    registerBuiltinContentTypes()
    render(<Markdown>{`The support result follows.\n${widget('chart-bars')}`}</Markdown>)
    expect(screen.getByText('The support result follows.')).toBeInTheDocument()
    expect(document.querySelector('[data-gideon-uispec="chart-bars"]')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('"schemaVersion"')
  })

  it('makes a task action visibly unavailable without an authoritative callback', () => {
    registerBuiltinContentTypes()
    render(<Markdown>{widget('create-task')}</Markdown>)
    expect(document.querySelector('[data-gideon-uispec="create-task"]')).toBeInTheDocument()
    expect(screen.getAllByText(/No connected provider or authorized route/).length).toBeGreaterThan(0)
  })

  it('refuses partial and changed envelopes without feeding them to the renderer', () => {
    const record = boundRecord('stays')
    const parsed = { schemaVersion: 1, template: 'stays', recordId: record.recordId, bindings: record.bindings }
    expect(resolveUISpecContent(JSON.stringify(parsed))?.recordId).toBe(record.recordId)
    delete parsed.bindings.title
    expect(resolveUISpecContent(JSON.stringify(parsed))).toBeNull()
    expect(resolveUISpecContent(JSON.stringify({ ...parsed, extra: 'text' }))).toBeNull()
    expect(resolveUISpecContent('{broken')).toBeNull()
    expect(resolveUISpecContent('x'.repeat(65_537))).toBeNull()
  })
})
