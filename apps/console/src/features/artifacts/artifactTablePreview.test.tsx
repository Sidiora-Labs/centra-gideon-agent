import { createElement, useState } from 'react'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { FileText } from 'lucide-react'
import type { Artifact } from '../../shared/data/api'
import type { ContentType, PreviewProps } from '../../shared/ui/content/contentTypes'
import { TextPreview } from '../../shared/ui/content/renderers'
import { DataTable } from '../../shared/vendor/assistant-ui/elements/data-table'
import { artifactTableRows, StructuredArtifactTable } from '../chat/auiStructuredResults'
import { artifactPreviewType } from './artifactTablePreview'

const artifact: Artifact = {
  slug: 'quarterly-results', name: 'Quarterly results', kind: 'json', source: 'chat',
  description: '', tags: [], version: 2, created_at: '2026-09-26T00:00:00Z',
  updated_at: '2026-09-26T00:00:00Z', content: '[{"quarter":"Q1","revenue":0,"closed":false}]',
  events: [], source_path: '', readonly: false,
}
const type: ContentType = {
  id: 'json', label: 'JSON', icon: FileText, tone: 'gray', kinds: ['json'],
  edit: { language: 'json', split: true }, preview: { render: TextPreview },
}
const props: PreviewProps = { content: artifact.content!, title: artifact.name, mode: 'dark' }

function preview(value: Artifact, content = value.content || '') {
  const resolved = artifactPreviewType(value, type)
  return createElement(resolved.preview!.render, { ...props, content })
}

function OpenArtifact() {
  const [opened, setOpened] = useState('')
  return <><StructuredArtifactTable artifact={artifact} onOpen={setOpened} />
    <output aria-label="Opened artifact">{opened}</output></>
}

describe('generic source DataTable', () => {
  it('uses real record keys and independent human-readable column labels', () => {
    render(<DataTable columns={[{ key: 'quarter', label: 'Financial quarter' }, { key: 'revenue', label: 'Revenue' }]}
      rows={[{ quarter: 'Q1', revenue: 0 }]} />)
    expect(screen.getAllByRole('columnheader').map(node => node.textContent)).toEqual(['Financial quarter', 'Revenue'])
    expect(screen.getAllByRole('cell').map(node => node.textContent)).toEqual(['Q1', '0'])
    expect(screen.queryByText('Model')).toBeNull()
    expect(screen.queryByText('Context')).toBeNull()
    expect(screen.queryByText('Cost')).toBeNull()
  })

  it('keeps false and zero distinct from missing values and null', () => {
    render(<DataTable columns={['zero', 'flag', 'null', 'missing'].map(key => ({ key, label: key }))}
      rows={[{ zero: 0, flag: false, null: null }]} />)
    const cells = screen.getAllByRole('cell')
    expect(cells[0].textContent).toBe('0')
    expect(cells[1].textContent).toBe('false')
    expect(cells[2].textContent).toBe('—')
    expect(cells[3].textContent).toBe('—')
  })

  it('does not read inherited properties as record values', () => {
    render(<DataTable columns={[{ key: 'toString', label: 'Missing key' }]} rows={[{}]} />)
    expect(screen.getByRole('cell').textContent).toBe('—')
    expect(screen.queryByText(/native code/)).toBeNull()
  })

  it('preserves empty strings and escapes markup in values and headings', () => {
    const label = '<script>heading</script>'
    const value = '<img src=x onerror=alert(1)>'
    const { container } = render(<DataTable columns={[{ key: 'text', label }]}
      rows={[{ text: value }, { text: '' }]} />)
    expect(screen.getByRole('columnheader').textContent).toBe(label)
    expect(screen.getAllByRole('cell').map(node => node.textContent)).toEqual([value, ''])
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
  })

  it('shows a real empty state when rows are absent while retaining column headings', () => {
    render(<DataTable columns={[{ key: 'name', label: 'Name' }]} rows={[]} />)
    expect(screen.getByRole('columnheader').textContent).toBe('Name')
    expect(screen.getByText('No rows available.')).toBeTruthy()
    expect(screen.queryAllByRole('cell')).toHaveLength(0)
  })

  it('does not produce a blank table when no columns can be inferred', () => {
    render(<DataTable columns={[]} rows={[{}]} />)
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByText('No rows available.')).toBeTruthy()
  })

  it('forwards container semantics and keeps long tables horizontally scrollable', () => {
    const { container } = render(<DataTable columns={[{ key: 'name', label: 'Name' }]} rows={[{ name: 'Long value' }]}
      className="custom-table" role="region" aria-label="Results" />)
    expect(screen.getByRole('region', { name: 'Results' }).className).toContain('custom-table')
    expect(container.querySelector('[data-slot="data-table"]')?.className).toContain('overflow-x-auto')
    expect(screen.getByRole('columnheader').getAttribute('scope')).toBe('col')
  })

  it('limits animation delay on long result sets and honors reduced motion', () => {
    render(<DataTable cycle={4} columns={[{ key: 'n', label: 'Number' }]}
      rows={Array.from({ length: 15 }, (_, n) => ({ n }))} />)
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows).toHaveLength(15)
    expect(rows[0].style.animationDelay).toBe('0ms')
    expect(rows[10].style.animationDelay).toBe('800ms')
    expect(rows[14].style.animationDelay).toBe('800ms')
    expect(rows[14].className).toContain('motion-reduce:animate-none')
  })

  it('retains the original model table contract', () => {
    render(<DataTable cycle={2} rows={[{ name: 'Configured model', context: '32k', cost: '$0.00' }]} />)
    expect(screen.getByText('Model')).toBeTruthy()
    expect(screen.getByText('Context')).toBeTruthy()
    expect(screen.getByText('Cost')).toBeTruthy()
    expect(screen.getByText('Configured model')).toBeTruthy()
    expect(screen.getByText('$0.00')).toBeTruthy()
  })
})

describe('artifact records rendered through source DataTable', () => {
  it('mounts exactly one sourced table for the real artifact values', () => {
    const { container } = render(<StructuredArtifactTable artifact={artifact} />)
    expect(screen.getAllByRole('table')).toHaveLength(1)
    expect(container.querySelectorAll('[data-slot="data-table"]')).toHaveLength(1)
    expect(screen.getAllByRole('columnheader').map(node => node.textContent)).toEqual(['quarter', 'revenue', 'closed'])
    expect(screen.getAllByRole('cell').map(node => node.textContent)).toEqual(['Q1', '0', 'false'])
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('can keep an open action while the host owns the heading', () => {
    let opened = ''
    render(<StructuredArtifactTable artifact={artifact} showTitle={false} onOpen={slug => { opened = slug }} />)
    expect(screen.queryByText(artifact.name)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open artifact' }))
    expect(opened).toBe(artifact.slug)
  })

  it('unions heterogeneous columns in their recorded order', () => {
    render(<StructuredArtifactTable artifact={{ ...artifact, content: '[{"name":"First"},{"total":2,"name":"Second"}]' }} />)
    expect(screen.getAllByRole('columnheader').map(node => node.textContent)).toEqual(['name', 'total'])
    const rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]).getAllByRole('cell').map(node => node.textContent)).toEqual(['First', '—'])
    expect(within(rows[1]).getAllByRole('cell').map(node => node.textContent)).toEqual(['Second', '2'])
  })

  it('opens the exact artifact slug when a real action is supplied', () => {
    render(<OpenArtifact />)
    fireEvent.click(screen.getByRole('button', { name: 'Open artifact' }))
    expect(screen.getByRole('status', { name: 'Opened artifact' }).textContent).toBe('quarterly-results')
  })

  it.each(['[]', '[{}]'])('shows empty recorded results for %s', content => {
    render(<StructuredArtifactTable artifact={{ ...artifact, content }} />)
    expect(screen.getByText('No rows available.')).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it.each(['{', '{"nested":1}', '[null]', '[[1]]', '[{"nested":{}}]', '[1]'])('declines non-tabular content %s', content => {
    const value = { ...artifact, content }
    expect(artifactTableRows(value)).toBeNull()
    render(<StructuredArtifactTable artifact={value} />)
    expect(screen.getByText('No tabular JSON in Quarterly results.')).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })
})

describe('artifact viewer preview selection', () => {
  it('reserves reading space without repeating the viewer title', () => {
    const { container } = render(preview(artifact))
    expect(container.firstElementChild?.className).toBe('p-4')
    expect(screen.queryByText(artifact.name)).toBeNull()
    expect(screen.getByRole('region', { name: `Data table from ${artifact.name}` })).toBeTruthy()
  })
  it('preserves editing, split mode and all existing content-type capabilities', () => {
    const resolved = artifactPreviewType(artifact, type)
    expect(resolved.id).toBe(type.id)
    expect(resolved.edit).toBe(type.edit)
    expect(resolved.edit?.split).toBe(true)
    expect(resolved.preview?.render).not.toBe(type.preview?.render)
    expect(type.preview?.render).toBe(TextPreview)
    expect(type.edit?.language).toBe('json')
  })

  it('uses the viewed version content rather than the latest artifact body', () => {
    render(preview(artifact, '[{"quarter":"Historical Q4","revenue":25}]'))
    expect(screen.getByRole('cell', { name: 'Historical Q4' })).toBeTruthy()
    expect(screen.getByRole('cell', { name: '25' })).toBeTruthy()
    expect(screen.queryByRole('cell', { name: 'Q1' })).toBeNull()
  })

  it('updates the table when the live preview draft changes', () => {
    const { rerender } = render(preview(artifact))
    expect(screen.getByRole('cell', { name: 'Q1' })).toBeTruthy()
    rerender(preview(artifact, '[{"quarter":"Q2"}]'))
    expect(screen.getByRole('cell', { name: 'Q2' })).toBeTruthy()
    expect(screen.queryByRole('cell', { name: 'Q1' })).toBeNull()
  })

  it.each(['{"nested":{"value":1}}', 'invalid json', ''])('retains the existing renderer for %j', content => {
    const { container } = render(preview(artifact, content))
    expect(container.textContent).toBe(content)
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('preserves non-JSON previews by identity', () => {
    expect(artifactPreviewType({ ...artifact, kind: 'text' }, type)).toBe(type)
    expect(artifactTableRows({ ...artifact, kind: 'text' })).toBeNull()
    expect(artifactTableRows({ ...artifact, content: null })).toBeNull()
  })

  it('does not replace types without a preview', () => {
    const editOnly = { ...type, preview: undefined }
    expect(artifactPreviewType(artifact, editOnly)).toBe(editOnly)
  })
})
