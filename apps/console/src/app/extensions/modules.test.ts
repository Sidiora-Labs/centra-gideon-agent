import { describe, expect, it } from 'vitest'
import { availableModules, installModuleCatalog, moduleFacade, resolveModuleSource, type HostModule } from './modules'

const hostReact = { createElement: () => null }
const catalog: HostModule[] = [
  { specifier: 'react', exports: hostReact },
  { specifier: '@gideon/app-sdk/ui', exports: {}, capability: 'shell-primitives' },
  { specifier: '@gideon/app-sdk/genui', exports: {}, capability: 'generative-widget' },
]
const resolve = (name: string) => name === 'react' ? 'blob:host-react' : null

describe('module composition', () => {
  it('deduplicates capabilities while preserving catalog order and export identity', () => {
    expect(availableModules(catalog, ['unknown', 'shell-primitives', 'shell-primitives']).map((entry) => entry.specifier))
      .toEqual(['react', '@gideon/app-sdk/ui'])
    const registry = { custom: 1 } as Record<string, unknown>
    installModuleCatalog(catalog, registry)
    installModuleCatalog(catalog, registry)
    expect(registry.react).toBe(hostReact)
    expect(registry.custom).toBe(1)
  })

  it('exports reserved property names without creating reserved variable bindings', () => {
    const source = moduleFacade('example', { class: 1, default: 2, 'not-valid': 3 })
    expect(source).toContain('export { binding0 as class }')
    expect(source).not.toContain('const class')
    expect(source).not.toContain('not-valid')
    expect(source).toContain('host.default === undefined')
  })
})

describe('bundle import resolution', () => {
  it('resolves static imports, side effects, re-exports and dynamic imports', async () => {
    const source = `import React from 'react'; import 'react'; export { createElement } from 'react'; const lazy = import('react');`
    const transformed = await resolveModuleSource(source, resolve, 'https://gateway.test/apps/demo/main.js')
    expect(transformed.match(/blob:host-react/g)).toHaveLength(4)
    expect(transformed).toContain('import("blob:host-react")')
  })

  it('does not rewrite matching text inside strings, comments or regular expressions', async () => {
    const source = `const message = "from 'react'"; // import 'react'\nconst expression = /from 'react'/; /* export {} from 'react' */`
    expect(await resolveModuleSource(source, resolve, 'https://gateway.test/app.js')).toBe(source)
  })

  it('resolves relative imports against the source location, preserving capability gates', async () => {
    const source = `import './style.js'; export * from '../shared.js'; import { Button } from '@gideon/app-sdk/ui';`
    const transformed = await resolveModuleSource(source, resolve, 'https://gateway.test/apps/demo/main.js')
    expect(transformed).toContain("import 'https://gateway.test/apps/demo/style.js'")
    expect(transformed).toContain("from 'https://gateway.test/apps/shared.js'")
    expect(transformed).toContain("from '@gideon/app-sdk/ui'")
  })

  it('decodes escaped specifiers while keeping computed imports and import.meta intact', async () => {
    const source = String.raw`import { createElement } from 're\u0061ct'; const later = import(prefix + name); const origin = import.meta.url;`
    const transformed = await resolveModuleSource(source, resolve, 'https://gateway.test/app.js')
    expect(transformed).toContain("from 'blob:host-react'")
    expect(transformed).toContain('import(prefix + name)')
    expect(transformed).toContain('import.meta.url')
  })
})
