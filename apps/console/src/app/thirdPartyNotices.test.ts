import { describe, expect, it } from 'vitest'
import { dirname, resolve } from 'node:path'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { APP_SHELL, mayCache, strategyFor } from './shell/swPolicy'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../')
const moduleUrl = pathToFileURL(resolve(root, 'apps/console/tooling/thirdPartyNotices.mjs')).href
const loadNotices = async () => await import(moduleUrl) as {
  thirdPartyNotices: (webDir: string, options: { repoRoot: string }) => {
    plugin: () => { buildStart: () => void; buildEnd: (error?: Error) => void; renderError: () => void; closeBundle: { handler: () => unknown } }
  }
  hasStaticNoticeReference: (reference: string, publicNotice: string, repoRoot: string) => boolean
  buildCensus: (input: { outputs: Map<string, Set<string>>, webDir: string, repoRoot: string }) => {
    text: string
    json: { packages: Array<{ name: string, version: string, license: string }>, files: Record<string, string[]> }
  }
}

describe('console third-party notice generation', () => {
  it('renders attribution for the actual package module and pinned installation', async () => {
    const { buildCensus } = await loadNotices()
    const packageDir = [resolve(root, 'apps/console/node_modules/lucide-react'), resolve(root, 'node_modules/lucide-react')]
      .find((path) => existsSync(resolve(path, 'package.json')))
    if (!packageDir) throw new Error('the installed lucide-react package is unavailable')
    const version = JSON.parse(readFileSync(resolve(packageDir, 'package.json'), 'utf8')).version as string
    const module = resolve(packageDir, 'dist/esm/icons/download-cloud.mjs')
    const result = buildCensus({
      outputs: new Map([['assets/notice-test.js', new Set([module, `\0${module}?commonjs-module`])]]),
      webDir: resolve(root, 'apps/console/src'),
      repoRoot: root,
    })

    expect(result.json.packages).toContainEqual(expect.objectContaining({ name: 'lucide-react', version, license: 'ISC' }))
    expect(result.json.files['assets/notice-test.js']?.some((path) => path.endsWith('/node_modules/lucide-react') || path === 'node_modules/lucide-react')).toBe(true)
    expect(result.text).toMatch(/ISC License/)
    expect(result.text).toMatch(/https:\/\/github\.com\/lucide-icons\/lucide/)
    const noticeUrl = new URL('/THIRD_PARTY_NOTICES.txt', 'https://gideon.test')
    expect(APP_SHELL).toContain('/THIRD_PARTY_NOTICES.txt')
    expect(mayCache(noticeUrl, 'https://gideon.test')).toBe(true)
    expect(strategyFor(noticeUrl, 'https://gideon.test', false)).toBe('cache-first')
  })


  it('attributes exact injected CommonJS helpers to the installed bundler', async () => {
    const { buildCensus } = await loadNotices()
    const result = buildCensus({
      outputs: new Map([['assets/helpers.js', new Set(['\0__vite-browser-external?commonjs-proxy', '\0commonjsHelpers.js'])]]),
      webDir: resolve(root, 'apps/console/src'),
      repoRoot: root,
    })
    expect(result.json.packages.some((entry) => entry.name === 'vite')).toBe(true)
    expect(() => buildCensus({
      outputs: new Map([['assets/unknown.js', new Set(['\0unrecognized-generated-module'])]]),
      webDir: resolve(root, 'apps/console/src'),
      repoRoot: root,
    })).toThrow(/names no installed package/)
  })

  it('attributes rendered widget runtime modules to their installed packages', async () => {
    const { buildCensus } = await loadNotices()
    const modules = [
      '\0gideon:widget-runtime/tailwindcss/index.js',
      '\0gideon:widget-runtime/react/react.production.js',
      '\0gideon:widget-runtime/react-dom/react-dom.production.js',
      '\0gideon:widget-runtime/react-dom/react-dom-client.production.js',
      '\0gideon:widget-runtime/scheduler/scheduler.production.js',
    ]
    const result = buildCensus({
      outputs: new Map([['assets/widget-runtime.js', new Set(modules)]]),
      webDir: resolve(root, 'apps/console/src'),
      repoRoot: root,
    })

    const expectedPackages = ['react', 'react-dom', 'scheduler', 'tailwindcss']
    for (const name of expectedPackages) {
      const packageJson = JSON.parse(readFileSync(resolve(root, 'node_modules', name, 'package.json'), 'utf8')) as {
        version: string
      }
      expect(result.json.packages).toContainEqual(
        expect.objectContaining({ name, version: packageJson.version, license: 'MIT' }),
      )
    }
    expect(result.json.files['assets/widget-runtime.js']).toEqual(
      expectedPackages.map((name) => `node_modules/${name}`),
    )
    expect(result.text).toContain('Meta Platforms, Inc. and affiliates')
    expect(result.text).toContain('Tailwind Labs, Inc.')
  })

  it('preserves an earlier build failure instead of checking incomplete output', async () => {
    const { thirdPartyNotices } = await loadNotices()
    const transformFailure = thirdPartyNotices(resolve(root, 'absent-console'), { repoRoot: root }).plugin()
    transformFailure.buildEnd(new Error('transform failed'))
    expect(() => transformFailure.closeBundle.handler()).not.toThrow()
    transformFailure.buildStart()
    expect(() => transformFailure.closeBundle.handler()).toThrow()
    const renderFailure = thirdPartyNotices(resolve(root, 'absent-console'), { repoRoot: root }).plugin()
    renderFailure.renderError()
    expect(() => renderFailure.closeBundle.handler()).not.toThrow()
  })

  it('fails closed when no bundled npm modules were reported', async () => {
    const { buildCensus } = await loadNotices()
    expect(() => buildCensus({ outputs: new Map(), webDir: resolve(root, 'apps/console'), repoRoot: root }))
      .toThrow(/reported no emitted npm package modules/)
  })

  it('accepts checked-in static assets pinned to the product licence', async () => {
    const { hasStaticNoticeReference } = await loadNotices()
    expect(hasStaticNoticeReference('LICENSE', '', root)).toBe(true)
    expect(hasStaticNoticeReference('LICENSE', '', resolve(root, 'apps/console'))).toBe(false)
  })
})
