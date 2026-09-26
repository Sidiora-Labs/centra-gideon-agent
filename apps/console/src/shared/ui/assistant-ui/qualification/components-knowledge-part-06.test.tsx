import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { Artifact } from '../../../data/api'
import { ArtifactCodeText, ArtifactFile, ArtifactMarkdownText } from '../../../../features/chat/auiStructuredResults'

const artifact: Artifact = {
  slug: 'file/one', name: 'Run notes.md', kind: 'markdown', source: 'chat', description: '', tags: [], version: 7,
  created_at: '2026-09-26', updated_at: '2026-09-26', content: '# Recorded result\n\n- step one',
  events: [], source_path: 'reports/run-notes.md', readonly: false,
}

describe('persisted artifact renderers', () => {
  it('downloads the exact saved artifact version and slug', () => {
    render(<ArtifactFile artifact={artifact} />)
    const link = screen.getByRole('link', { name: 'Download file' })
    expect(link.getAttribute('href')).toBe('/api/artifacts/file%2Fone/raw?version=7')
    expect(link.getAttribute('download')).toBe('Run notes.md')
    expect(screen.getByText('markdown · version 7')).toBeTruthy()
  })

  it('renders recorded markdown with real list structure', () => {
    render(<ArtifactMarkdownText artifact={artifact} />)
    expect(screen.getByRole('heading', { name: 'Recorded result' })).toBeTruthy()
    expect(screen.getByRole('listitem').textContent).toBe('step one')
  })

  it('does not parse a JSON artifact as markdown', () => {
    const { container } = render(<ArtifactMarkdownText artifact={{ ...artifact, kind: 'json' }} />)
    expect(container.querySelector('[data-slot="markdown-text"]')).toBeNull()
  })

  it('keeps absent content absent instead of substituting a sample', () => {
    const { container, rerender } = render(<ArtifactMarkdownText artifact={{ ...artifact, content: null }} />)
    expect(container.querySelector('article')).toBeNull()
    rerender(<ArtifactCodeText artifact={{ ...artifact, kind: 'text', content: null }} />)
    expect(container.querySelector('pre')).toBeNull()
  })

  it('shows exact text artifact code without interpreting its HTML', () => {
    const { container } = render(<ArtifactCodeText artifact={{ ...artifact, kind: 'text', content: '<script>alert(1)</script>' }} />)
    expect(container.querySelector('code')?.textContent).toBe('<script>alert(1)</script>')
    expect(container.querySelector('script')).toBeNull()
  })

  it('does not label arbitrary markdown as code', () => {
    const { container } = render(<ArtifactCodeText artifact={artifact} />)
    expect(container.querySelector('[data-slot="code-text"]')).toBeNull()
  })
})
