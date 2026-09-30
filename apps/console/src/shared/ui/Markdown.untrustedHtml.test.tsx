import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from './Markdown'

describe('untrusted Markdown HTML', () => {
  it('renders untrusted HTML as inert text', () => {
    const html = [
      '<img src="https://attacker.example/pixel" onerror="alert(1)">',
      '<iframe src="https://attacker.example/frame">frame content</iframe>',
      '<script>window.compromised = true</script>',
      '<a href="javascript:alert(1)" onclick="alert(1)">unsafe link</a>',
    ].join('\n\n')
    const { container } = render(<Markdown>{`**Trusted Markdown**\n\n$$x^2$$\n\n${html}`}</Markdown>)

    expect(container.querySelector('strong')).toHaveTextContent('Trusted Markdown')
    expect(container.querySelector('.katex')).toBeInTheDocument()
    expect(container.textContent).toContain('<img src="https://attacker.example/pixel" onerror="alert(1)">')
    expect(container.textContent).toContain('<script>window.compromised = true</script>')
    expect(container.querySelector('img, iframe, script, a[href^="javascript:"], [onerror], [onclick]')).toBeNull()
  })
})
