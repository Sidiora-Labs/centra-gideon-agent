import { Suspense, type ComponentProps } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CodePreview } from './CodePreview'
import { TextPreview } from './renderers'
import { registerBuiltinContentTypes } from './registerBuiltins'
import { resolveContentType, type PreviewProps } from './contentTypes'
import { SyntaxHighlighter } from '../../vendor/assistant-ui/elements/syntax-highlighter'

const components = {
  Pre: (props: ComponentProps<'pre'>) => <pre {...props} />,
  Code: (props: ComponentProps<'code'>) => <code {...props} />,
}
const code = '  const greeting = "Hello";\n\nconsole.log(greeting);\n'

function preview(props: Partial<PreviewProps> = {}) {
  return <CodePreview content={code} mode="light" title="greeting.ts" {...props} />
}

async function exactCode(container: HTMLElement, expected: string) {
  await waitFor(() => {
    expect(container.querySelectorAll('pre')).toHaveLength(1)
    expect(container.querySelector('pre')?.textContent).toBe(expected)
  })
}

describe('Recorded code artifact preview', () => {
  it('shows a single light code pane without trimming leading spaces or final newlines', async () => {
    const { container } = render(preview())
    await exactCode(container, code)
    await waitFor(() => expect(container.querySelector('pre span[style*="color"]')).not.toBeNull())
    const pre = container.querySelector('pre')!
    expect(pre.classList.contains('dark:hidden')).toBe(false)
    expect(pre.classList.contains('hidden')).toBe(false)
    expect(container.querySelectorAll('textarea,[contenteditable="true"]')).toHaveLength(0)
    expect(screen.getByRole('region', { name: 'greeting.ts' })).not.toBeNull()
  })

  it('switches the actual theme without adding a duplicate code pane', async () => {
    const { container, rerender } = render(preview())
    await exactCode(container, code)
    const lightBackground = container.querySelector('pre')!.style.background
    rerender(preview({ mode: 'dark' }))
    await exactCode(container, code)
    const darkPre = container.querySelector('pre')!
    expect(darkPre.style.background).not.toBe(lightBackground)
    expect(darkPre.classList.contains('hidden')).toBe(false)
    expect(darkPre.classList.contains('dark:block')).toBe(false)
  })

  it('uses the supplied historical content when the artifact title and path stay the same', async () => {
    const { container, rerender } = render(preview({ path: '/project/greeting.ts' }))
    await exactCode(container, code)
    const previous = 'const greeting = "Earlier version";\n'
    rerender(preview({ path: '/project/greeting.ts', content: previous }))
    await exactCode(container, previous)
    expect(container.textContent).not.toContain('console.log')
    rerender(preview({ path: '/project/greeting.ts', content: code }))
    await exactCode(container, code)
  })

  it.each([
    ['run.js', 'const count = 0;'],
    ['run.jsx', 'const element = <main />;'],
    ['run.mjs', 'export const count = 0;'],
    ['run.cjs', 'module.exports = 0;'],
    ['run.ts', 'const count: number = 0;'],
    ['run.tsx', 'const element = <main />;'],
    ['run.py', 'value = 0\nprint(value)'],
    ['RUN.PY', 'value = False'],
  ])('renders actual contents for supported file %s', async (path, content) => {
    const { container } = render(preview({ path, content, title: 'Recorded source' }))
    await exactCode(container, content)
    expect(container.querySelector('code')?.className).toContain('language-')
  })

  it('uses the title for a new code artifact without a source path', async () => {
    const { container } = render(preview({ title: 'script.py', path: '' , content: 'print("new")' }))
    await exactCode(container, 'print("new")')
    expect(container.querySelector('code')?.className).toContain('language-python')
  })

  it.each(['notes.unknown', 'file.constructor', 'file.__proto__', 'untitled'])('keeps unknown language %s readable as literal text', async title => {
    const content = '<img src=x onerror="globalThis.unexpectedPreviewExecution=true">\n'
    const { container } = render(preview({ title, content }))
    await exactCode(container, content)
    expect(container.querySelector('code')?.className).toContain('language-text')
    expect(container.querySelectorAll('img,script,iframe')).toHaveLength(0)
    expect('unexpectedPreviewExecution' in globalThis).toBe(false)
  })

  it('preserves an empty file and streaming replacements as the actual displayed content', async () => {
    const { container, rerender } = render(preview({ content: '', streaming: true }))
    await exactCode(container, '')
    rerender(preview({ content: 'const value', streaming: true }))
    await exactCode(container, 'const value')
    rerender(preview({ content: 'const value = 0;\n', streaming: false }))
    await exactCode(container, 'const value = 0;\n')
  })

  it('registers code kind and supported extensions with the real lazy preview', async () => {
    registerBuiltinContentTypes()
    for (const name of ['script.js', 'script.mjs', 'script.cjs', 'script.ts', 'script.py']) {
      expect(resolveContentType({ name }).id).toBe('code')
    }
    expect(resolveContentType({ name: 'component.jsx' }).id).toBe('react')
    expect(resolveContentType({ name: 'component.tsx' }).id).toBe('react')
    const type = resolveContentType({ name: 'saved.ts' })
    expect(type.id).toBe('code')
    expect(type.edit?.language).toBe('plaintext')
    const Renderer = type.preview!.render
    const { container } = render(<Suspense fallback={<p>Loading preview</p>}>
      <Renderer content={code} title="Registered artifact" mode="dark" path="saved.ts" />
    </Suspense>)
    await screen.findByRole('region', { name: 'Registered artifact' })
    await exactCode(container, code)
    expect(screen.queryByText('Loading preview')).toBeNull()
  })

  it('mounts highlighting for real text-kind artifacts using their recorded filename', async () => {
    registerBuiltinContentTypes()
    expect(resolveContentType({ kind: 'text', name: 'script.py' }).id).toBe('text')
    const { container, rerender } = render(<TextPreview content={'print("actual artifact")\n'} title="script.py" mode="light" />)
    await exactCode(container, 'print("actual artifact")\n')
    expect(container.querySelector('[data-slot="artifact-code-preview"]')).not.toBeNull()
    expect(container.querySelector('code')?.className).toContain('language-python')
    rerender(<TextPreview content="const version = 2;" title="Source artifact" path="/workspace/current.ts" mode="dark" />)
    await exactCode(container, 'const version = 2;')
    expect(container.querySelector('code')?.className).toContain('language-ts')
    rerender(<TextPreview content="ordinary notes" title="notes.txt" mode="light" />)
    await exactCode(container, 'ordinary notes')
    expect(container.querySelector('[data-slot="artifact-code-preview"]')).toBeNull()
  })

  it('keeps ordinary text on its existing plain preview', () => {
    registerBuiltinContentTypes()
    expect(resolveContentType({ name: 'notes.txt' }).id).toBe('text')
    expect(resolveContentType({ name: 'events.log' }).id).toBe('text')
    const { container } = render(<TextPreview content={'  plain text\n'} title="notes.txt" mode="light" />)
    expect(container.querySelector('pre')?.textContent).toBe('  plain text\n')
    expect(container.querySelector('[data-slot="artifact-code-preview"]')).toBeNull()
  })

  it('preserves donor automatic theme behavior when no explicit mode is supplied', async () => {
    const { container } = render(<SyntaxHighlighter components={components} language="text" code="unchanged donor default" />)
    await waitFor(() => expect(container.querySelectorAll('pre')).toHaveLength(2))
    expect(container.querySelector('pre.dark\\:hidden')?.textContent).toBe('unchanged donor default')
    expect(container.querySelector('pre.dark\\:block')?.textContent).toBe('unchanged donor default')
  })
})
