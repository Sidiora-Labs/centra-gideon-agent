import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FileText } from 'lucide-react'
import { useState } from 'react'
import { ContentSurface } from './ContentSurface'
import { DocumentPreview, TextPreview, OfficeDocPreview } from './renderers'
import { allContentTypes, embedFor, extOf, getContentType, isCommentable, registerContentType, resolveContentType, type ContentType } from './contentTypes'
import { registerBuiltinContentTypes } from './registerBuiltins'
import { sanitizeInlineHtml } from './sanitize'
import { documentExportSource } from './exporters'
import { artifactReference, binaryReference, documentFormat } from './contentPreviewState'
import { contentPermissions, proportionalScroll, reconcileContentDraft, useContentDraft, type DraftState, type DraftCache } from './contentSurfaceState'
import { sameSessionTarget, planningTarget } from './commentTarget'

const textType: ContentType = { id: 'runtime-text', label: 'Runtime text', icon: FileText, tone: 'var(--color-primary)', preview: { render: TextPreview }, edit: { language: 'plaintext', split: true } }

describe('registry capability composition', () => {
  it('installs the complete catalog once and preserves capability boundaries', () => {
    registerBuiltinContentTypes()
    const first = allContentTypes().map(type => type.id)
    registerBuiltinContentTypes()
    expect(allContentTypes().map(type => type.id)).toEqual(first)
    expect(first).toEqual(['widget', 'genui', 'html', 'react', 'markdown', 'document', 'svg', 'infographic', 'json', 'csv', 'image', 'pdf', 'docx', 'xlsx', 'pptx', 'video', 'code', 'text'])
    expect(embedFor('unknown')).toBe(getContentType('widget')?.embed)
    expect(embedFor('react')?.streaming).toBe(false)
    expect(isCommentable(getContentType('widget')!)).toBe(false)
    expect(getContentType('xlsx')?.binary).toBe(true)
  })
  it('keeps match, kind, extension, MIME and fallback precedence', () => {
    registerContentType({ ...textType, id: 'runtime-match', match: probe => probe.name === 'priority.json' })
    expect(resolveContentType({ kind: 'document', name: 'priority.json' }).id).toBe('runtime-match')
    expect(resolveContentType({ kind: 'document', name: 'file.json' }).id).toBe('document')
    expect(resolveContentType({ name: 'file.JSON', mime: 'image/png' }).id).toBe('json')
    expect(resolveContentType({ mime: 'image/png' }).id).toBe('image')
    expect(resolveContentType({}, 'code').id).toBe('code')
    registerContentType({ ...textType, id: 'runtime-match', kinds: ['runtime-replacement'] })
    expect(resolveContentType({ name: 'priority.json' }).id).toBe('json')
    expect(resolveContentType({ kind: 'runtime-replacement' }).id).toBe('runtime-match')
    expect(allContentTypes().filter(type => type.id === 'runtime-match')).toHaveLength(1)
  })
  it('keeps dotfiles and directory dots out of extension dispatch', () => {
    expect(extOf('/dir.with.dot/.env')).toBe('')
    expect(extOf('/dir.with.dot/report.CSV')).toBe('csv')
    expect(extOf('README')).toBe('')
  })
})

describe('in-document sanitization', () => {
  it('retains editorial structure while dropping scripts, events, styles and unsafe URL schemes', () => {
    const output = sanitizeInlineHtml('<article><h1 id="title">Hello</h1><p style="color:red" onclick="bad()">Safe <a href="java&#10;script:bad()">link</a></p><script>bad()</script><iframe src="https://example.test"></iframe></article>')
    const document = new DOMParser().parseFromString(output, 'text/html')
    expect(document.querySelector('h1')?.textContent).toBe('Hello')
    expect(document.querySelectorAll('script,iframe,[onclick],[style]')).toHaveLength(0)
    expect(document.querySelector('a')?.hasAttribute('href')).toBe(false)
  })
  it('filters root SVG URLs as well as descendants and preserves local paint references', () => {
    const source = '<svg xmlns="http://www.w3.org/2000/svg" href="javascript:bad()" onload="bad()"><defs><linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient></defs><rect fill="url(#g)" width="8" height="8"/><use href="java&#10;script:bad()"/><path fill="url(javascript:bad())"/></svg>'
    const output = sanitizeInlineHtml(source, 'svg')
    const document = new DOMParser().parseFromString(output, 'image/svg+xml')
    expect(document.documentElement.hasAttribute('href')).toBe(false)
    expect(document.documentElement.hasAttribute('onload')).toBe(false)
    expect(document.querySelector('rect')?.getAttribute('fill')).toBe('url(#g)')
    expect(document.querySelector('use')?.hasAttribute('href')).toBe(false)
    expect(document.querySelector('path')?.hasAttribute('fill')).toBe(false)
  })
  it('accepts safe links/images and rejects documents that cannot yield SVG', () => {
    const output = sanitizeInlineHtml('<a href="https://example.test">Web</a><a href="mailto:a@example.test">Mail</a><img src="data:image/png;base64,AAAA"><img src="data:text/html,bad">')
    const document = new DOMParser().parseFromString(output, 'text/html')
    expect(document.querySelectorAll('a[href]')).toHaveLength(2)
    expect(document.querySelectorAll('img[src]')).toHaveLength(1)
    expect(sanitizeInlineHtml('<div>no SVG</div>', 'svg')).toBe('')
    expect(sanitizeInlineHtml('   ')).toBe('')
  })
})

describe('content classification and standalone export', () => {
  it('rescues markdown prose without misclassifying structured HTML', () => {
    expect(documentFormat('# Heading\n**Bold** with <a>inline link</a>')).toBe('markdown')
    expect(documentFormat('<section><p>**Literal**</p></section>')).toBe('html')
    expect(documentFormat('plain text')).toBe('html')
  })
  it('resolves binary references and rejects malformed artifact escape sequences', () => {
    expect(binaryReference('data:image/png;base64,AAAA', true)).toBe('data:image/png;base64,AAAA')
    expect(binaryReference('data:image/png;base64,AAAA')).toBeUndefined()
    expect(artifactReference('/api/artifacts/sales%20report/raw')).toBe('sales report')
    expect(artifactReference('/api/artifacts/%ZZ/raw')).toBeNull()
    expect(artifactReference('/elsewhere/document.docx')).toBeNull()
  })
  it('builds a complete export with an inert title, sanitized body and inline measure', () => {
    const output = documentExportSource('<h1>Document</h1><script>bad()</script>', '</title><script>title</script>')
    const document = new DOMParser().parseFromString(output, 'text/html')
    expect(document.title).toBe('</title><script>title</script>')
    expect(document.querySelector('main')?.textContent).toBe('Document')
    expect(document.querySelectorAll('script')).toHaveLength(0)
    expect(document.querySelector('style')?.textContent).toContain('max-width: 35rem')
  })
})

describe('draft and view ownership', () => {
  const base: DraftState = { id: 'a', draft: 'disk', base: 'disk', view: 'split', customDirty: false }
  it('follows disk only while clean and restores per-document drafts', () => {
    expect(reconcileContentDraft(base, 'a', 'updated', true).draft).toBe('updated')
    expect(reconcileContentDraft({ ...base, draft: 'mine' }, 'a', 'updated', true).draft).toBe('mine')
    const cache: DraftCache = new Map([['b', { draft: 'cached', base: 'server', warned: true }]])
    expect(reconcileContentDraft({ ...base, customDirty: true }, 'b', 'server', false, cache)).toEqual({ id: 'b', draft: 'cached', base: 'server', view: 'edit', customDirty: false })
  })
  it('derives text and custom editor permissions without enabling destructive text saves', () => {
    expect(contentPermissions(textType, false, false, () => {})).toMatchObject({ editable: true, draftEditable: true, splittable: true })
    expect(contentPermissions(textType, true, false, () => {})).toMatchObject({ editable: false, draftEditable: false })
    expect(contentPermissions(textType, false, true, () => {})).toMatchObject({ editable: false })
  })
  it('maps scroll positions between extents and clamps overscroll', () => {
    expect(proportionalScroll(25, 100, 400)).toBe(100)
    expect(proportionalScroll(-20, 100, 400)).toBe(0)
    expect(proportionalScroll(120, 100, 400)).toBe(400)
    expect(proportionalScroll(20, 0, 400)).toBe(0)
  })
  it('uses actual input state, locks duplicate saves and preserves cached warning metadata', async () => {
    const cache: DraftCache = new Map([['a', { draft: 'cached', base: 'disk', warned: true }]])
    const versions: string[] = []
    function Editor() {
      const [content, setContent] = useState('disk')
      const state = useContentDraft({ id: 'a', content, previewable: true, editable: true, cache,
        save: async draft => { versions.push(draft); setContent(draft) } })
      return <><textarea aria-label="Live draft" value={state.draft} onChange={event => state.setDraft(event.target.value)} /><button onClick={() => { void state.save(); void state.save() }}>Save draft</button><output>{state.dirty ? 'dirty' : 'clean'}</output></>
    }
    render(<Editor />)
    expect(cache.get('a')?.warned).toBe(true)
    fireEvent.change(screen.getByLabelText('Live draft'), { target: { value: 'authored' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save draft' })) })
    expect(versions).toEqual(['authored'])
    expect(screen.getByText('clean')).toBeTruthy()
    expect(cache.has('a')).toBe(false)
  })
})

describe('content surface and routing', () => {
  it('renders an accessible preview and owns keyboard export dismissal', () => {
    const exported: string[] = []
    const type = { ...textType, exports: [{ id: 'collect', label: 'Collect content', run: (content: string, title: string) => { exported.push(`${title}:${content}`) } }] }
    render(<ContentSurface type={type} content="Actual content" title="Report" docId="report" readOnly />)
    expect(screen.getByRole('group', { name: 'Report preview' }).tabIndex).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: 'Export' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('button', { name: 'Collect content' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Export' }))
    fireEvent.click(screen.getByRole('button', { name: 'Collect content' }))
    expect(exported).toEqual(['Report:Actual content'])
  })
  it('renders document sanitization feedback and handles malformed office references', async () => {
    const view = render(<DocumentPreview content="<script>bad()</script>" mode="dark" title="Blocked" />)
    expect(screen.getByText('Nothing to display.')).toBeTruthy()
    view.rerender(<OfficeDocPreview content="/api/artifacts/%ZZ/raw" mode="dark" title="Invalid reference" />)
    await waitFor(() => expect(screen.getByText(/Couldn't extract a text preview/)).toBeTruthy())
  })
  it('delivers same-chat and planning comments through their supplied context owners', async () => {
    const submissions: string[] = []
    const send = (message: string, paths: string[]) => { submissions.push(`${message}:${paths.join(',')}`) }
    await sameSessionTarget(send).submit({ message: 'Revise', docPaths: ['a.md'] })
    await planningTarget(send).submit({ message: 'Plan', docPaths: [] })
    expect(submissions).toEqual(['Revise:a.md', 'Plan:'])
  })
})
