import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { allContentTypes, getContentType, type ContentType } from './contentTypes'
import { registerBuiltinContentTypes } from './registerBuiltins'
import { ContentSurface } from './ContentSurface'
import {
  DOCUMENT_EDITING_TYPE_IDS, resetDocumentEditingForTests, setDocumentEditing,
} from './documentEditing'


vi.mock('@monaco-editor/react', () => ({
  default: () => <div data-testid="monaco" />,
  DiffEditor: () => null,
}))
vi.mock('./DocumentEditor', () => ({
  DocumentEditor: ({ slug }: { slug: string }) => <div data-testid="doc-editor">editing {slug}</div>,
}))
vi.mock('../../../app/shell/theme', () => ({ useMode: () => ({ mode: 'dark' }) }))

const CustomEditor = ({ slug }: { slug: string }) => <div data-testid="custom-editor">editing {slug}</div>

const baseType = (over: Partial<ContentType>): ContentType => ({
  id: 'probe', label: 'Probe', icon: (() => null) as unknown as ContentType['icon'], tone: '',
  ...over,
})

const withRenderer = () => baseType({ id: 'office', edit: { language: 'plaintext', render: CustomEditor } })

function mount(type: ContentType) {
  return render(
    <ContentSurface type={type} content="hello" title="Doc" docId="doc-1" onSave={() => {}} />,
  )
}

describe('a type without an editor renderer still gets Monaco', () => {
  it('mounts Monaco, not a custom editor', async () => {
    mount(baseType({ edit: { language: 'markdown' }, id: 'plain' }))
    expect(await screen.findByTestId('monaco')).toBeInTheDocument()
    expect(screen.queryByTestId('custom-editor')).not.toBeInTheDocument()
  })

  it('keeps its Save and Revert — the draft is still what it edits', () => {
    mount(baseType({ edit: { language: 'markdown' }, id: 'plain' }))
    expect(screen.getByTitle(/^Save/)).toBeInTheDocument()
    expect(screen.getByTitle('Revert unsaved changes')).toBeInTheDocument()
  })
})

describe('a type WITH an editor renderer gets it instead of Monaco', () => {
  it('mounts the declared component and no Monaco', () => {
    mount(withRenderer())
    expect(screen.getByTestId('custom-editor')).toHaveTextContent('editing doc-1')
    expect(screen.queryByTestId('monaco')).not.toBeInTheDocument()
  })

  it('suppresses the string-draft Save/Revert, which would destroy a binary body', () => {
    mount(withRenderer())
    expect(screen.queryByTitle(/^Save/)).not.toBeInTheDocument()
    expect(screen.queryByTitle('Revert unsaved changes')).not.toBeInTheDocument()
  })

  it('needs no host onSave — the editor owns its own persistence', () => {
    render(<ContentSurface type={withRenderer()} content="" title="Doc" docId="doc-1" />)
    expect(screen.getByTestId('custom-editor')).toBeInTheDocument()
  })

  it('shows nothing editable on a read-only host (a historical version)', () => {
    render(<ContentSurface type={withRenderer()} content="" title="Doc" docId="doc-1" readOnly />)
    expect(screen.queryByTestId('custom-editor')).not.toBeInTheDocument()
  })
})


describe('the renderer slot is claimed by the office types and nothing else', () => {
  beforeEach(() => { registerBuiltinContentTypes(); resetDocumentEditingForTests() })
  afterEach(() => { registerBuiltinContentTypes(); resetDocumentEditingForTests() })

  it('no builtin type declares a custom editor while the flag is off', () => {
    expect(allContentTypes().filter((t) => t.edit?.render).map((t) => t.id)).toEqual([])
  })

  it('and with the flag on, exactly the office types do', () => {
    setDocumentEditing(true)
    expect(allContentTypes().filter((t) => t.edit?.render).map((t) => t.id).sort())
      .toEqual([...DOCUMENT_EDITING_TYPE_IDS].sort())
  })

  it('every other editable type still declares a Monaco language and no renderer', () => {
    setDocumentEditing(true)
    const office = new Set<string>(DOCUMENT_EDITING_TYPE_IDS)
    for (const t of allContentTypes()) {
      if (!t.edit || office.has(t.id)) continue
      expect(t.edit.render, `${t.id} grew a custom editor`).toBeUndefined()
      expect(t.edit.language, `${t.id} lost its Monaco language`).toBeTruthy()
    }
  })

  it('off leaves the office types with no edit capability — the pre-DFE-5 registration', () => {
    for (const id of DOCUMENT_EDITING_TYPE_IDS) {
      expect(getContentType(id)?.edit, id).toBeUndefined()
    }
  })

  it('on attaches the editor, and turning it back off removes it again', () => {
    setDocumentEditing(true)
    expect(getContentType('docx')?.edit?.render).toBeDefined()
    setDocumentEditing(false)
    expect(getContentType('docx')?.edit).toBeUndefined()
  })

  it('leaves everything else about the type alone', () => {
    const before = getContentType('docx')!
    setDocumentEditing(true)
    const after = getContentType('docx')!
    expect(after.preview).toBe(before.preview)
    expect(after.binary).toBe(true)
    expect(after.commentable).toBe(false)
    expect(after.kinds).toEqual(before.kinds)
  })

  it('a docx artifact offers the way INTO edit once the flag is on', async () => {
    setDocumentEditing(true)
    render(<ContentSurface type={getContentType('docx')!} content="" title="Report" docId="report" />)
    expect(await screen.findByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('and in edit view it mounts the document editor — the registry type, not a fixture', () => {
    setDocumentEditing(true)
    render(<ContentSurface type={getContentType('docx')!} content="" title="Report" docId="report" initialView="edit" />)
    expect(screen.getByTestId('doc-editor')).toHaveTextContent('editing report')
  })

  it('and with the flag off the same artifact offers no editor at all', async () => {
    render(<ContentSurface type={getContentType('docx')!} content="" title="Report" docId="report" />)
    expect(await screen.findByRole('group', { name: 'Report preview' })).toBeInTheDocument()
    expect(screen.queryByTestId('doc-editor')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    expect(screen.queryByTitle(/^Save/)).not.toBeInTheDocument()
  })
})
