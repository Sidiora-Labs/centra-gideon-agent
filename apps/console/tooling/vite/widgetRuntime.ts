import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import type { Plugin } from 'vite'

const requireFromConsole = createRequire(import.meta.url)
const tailwindRoot = dirname(requireFromConsole.resolve('tailwindcss/package.json'))
const reactRoot = dirname(requireFromConsole.resolve('react/package.json'))
const reactDomRoot = dirname(requireFromConsole.resolve('react-dom/package.json'))
const requireFromReactDom = createRequire(join(reactDomRoot, 'package.json'))
const schedulerRoot = dirname(requireFromReactDom.resolve('scheduler/package.json'))

const packageFiles = new Map([
  ['gideon:widget-runtime/tailwindcss/index.js', join(tailwindRoot, 'index.css')],
  ['gideon:widget-runtime/react/react.production.js', join(reactRoot, 'cjs/react.production.js')],
  ['gideon:widget-runtime/react-dom/react-dom.production.js', join(reactDomRoot, 'cjs/react-dom.production.js')],
  ['gideon:widget-runtime/react-dom/react-dom-client.production.js', join(reactDomRoot, 'cjs/react-dom-client.production.js')],
  ['gideon:widget-runtime/scheduler/scheduler.production.js', join(schedulerRoot, 'cjs/scheduler.production.js')],
])

export function widgetRuntime(): Plugin {
  const sourceByResolvedId = new Map(
    [...packageFiles.entries()].map(([id, sourceFile]) => [`\0${id}`, sourceFile]),
  )

  return {
    name: 'gideon-widget-runtime',
    enforce: 'pre',
    resolveId(id) {
      if (!packageFiles.has(id)) return null
      return `\0${id}`
    },
    load(id) {
      const sourceFile = sourceByResolvedId.get(id)
      if (!sourceFile) return null
      return `export default ${JSON.stringify(readFileSync(sourceFile, 'utf8'))}`
    },
  }
}
