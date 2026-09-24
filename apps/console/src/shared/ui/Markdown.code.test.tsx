import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Markdown } from './Markdown'

describe('Markdown code parent semantics', () => {
  it.each(['```\none line\n```', '~~~\none line\n~~~', '    one line'])('renders code inside pre as a block: %s', (source) => {
    const { container } = render(<Markdown>{source}</Markdown>)
    expect(screen.getByRole('group', { name: 'Code' })).toHaveTextContent('one line')
    expect(container.querySelector('pre code')).toHaveTextContent('one line')
    expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
  })

  it('leaves ordinary inline code outside pre and without block controls', () => {
    const { container } = render(<Markdown>{'Use `one line` here.'}</Markdown>)
    expect(container.querySelector('p code')).toHaveTextContent('one line')
    expect(container.querySelector('pre')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Copy code' })).toBeNull()
  })

  it('does not mistake a file path in a fence for an inline file action', () => {
    const opened: string[] = []
    const { container } = render(<Markdown onFileClick={(path) => opened.push(path)}>{'`src/main.py`\n\n```\nsrc/main.py\n```'}</Markdown>)
    expect(screen.getAllByRole('button', { name: 'src/main.py' })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'src/main.py' }))
    expect(opened).toEqual(['src/main.py'])
    expect(container.querySelector('pre code')).toHaveTextContent('src/main.py')
  })

  it('preserves language and diff controls for fenced code', () => {
    const { rerender } = render(<Markdown>{'```bash\necho hello\n```'}</Markdown>)
    expect(screen.getByRole('group', { name: 'bash code' })).toHaveTextContent('echo hello')
    expect(screen.getByRole('button', { name: 'Run' })).toBeInTheDocument()
    rerender(<Markdown>{'```diff\n-old\n+new\n```'}</Markdown>)
    expect(screen.getByRole('group', { name: 'Diff' })).toHaveTextContent('-old')
    expect(screen.getByRole('button', { name: 'Copy diff' })).toBeInTheDocument()
  })

  it('uses an actual pre parent for raw HTML code too', () => {
    const { container } = render(<Markdown>{'<pre><code>one line</code></pre>'}</Markdown>)
    expect(container.querySelector('pre code')).toHaveTextContent('one line')
    expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
  })

  it('preserves raw preformatted text without requiring a code child', () => {
    const { container } = render(<Markdown>{'<pre>plain text</pre>'}</Markdown>)
    expect(container.querySelector('pre')).toHaveTextContent('plain text')
  })

  it('preserves the explicit inline prose mode for fence input', () => {
    const { container } = render(<p><Markdown inline>{'```\none line\n```'}</Markdown></p>)
    expect(container.querySelector('p code')).toHaveTextContent('one line')
    expect(container.querySelector('pre, div')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Copy code' })).toBeNull()
  })
})
