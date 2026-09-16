let library: Promise<typeof import('mermaid').default> | undefined
let queue: Promise<unknown> = Promise.resolve()
let sequence = 0

export function renderWidgetDiagram(code: string, mode: 'dark' | 'light'): Promise<string> {
  const identifier = `gideon-diagram-${++sequence}`
  const result = queue.then(async () => {
    library ??= import('mermaid').then(module => module.default)
    const mermaid = await library
    mermaid.initialize({ startOnLoad: false, theme: mode === 'dark' ? 'dark' : 'default', securityLevel: 'strict' })
    const rendered = await mermaid.render(identifier, code)
    return rendered.svg
  })
  queue = result.catch(() => undefined)
  return result
}
