import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  APP_SHELL,
  CACHEABLE_PREFIXES,
  SHELL_DOCUMENT,
  isApiPath,
  shellDocument,
  cacheableResponse,
  mayCache,
  strategyFor,
} from './swPolicy'


const WEB_DIR = join(__dirname, "../../..")
const ORIGIN = 'http://localhost:10000'
const url = (path: string, origin = ORIGIN) => new URL(path, origin)

describe('APP_SHELL — the precache list', () => {
  it('precaches the app shell ONLY: no /api entry', () => {
    for (const entry of APP_SHELL) expect(isApiPath(entry)).toBe(false)
  })

  it('holds nothing user-specific', () => {
    const userScoped = /session|approval|inbox|entity|device|user|token|credential|file/i
    for (const entry of APP_SHELL) expect(entry).not.toMatch(userScoped)
  })

  it('is exactly the documented shell — every entry is real build input', () => {
    expect(APP_SHELL).toContain(SHELL_DOCUMENT)
    for (const entry of APP_SHELL) {
      const source =
        entry === SHELL_DOCUMENT
          ? join(WEB_DIR, 'index.html')
          : join(WEB_DIR, 'public', entry)
      expect(existsSync(source), `${entry} has no source file at ${source}`).toBe(true)
    }
  })

  it('declares the same icons the manifest does', () => {
    const manifest = JSON.parse(
      readFileSync(join(WEB_DIR, 'public', 'manifest.webmanifest'), 'utf8'),
    ) as { icons: { src: string }[] }
    for (const icon of manifest.icons) expect(APP_SHELL).toContain(icon.src)
  })
})

describe('isApiPath', () => {
  it('matches the API namespace including the bare prefix', () => {
    expect(isApiPath('/api')).toBe(true)
    expect(isApiPath('/api/approvals')).toBe(true)
    expect(isApiPath('/api/ws/terminal/x')).toBe(true)
  })

  it('does not over-match a path that merely starts with the letters', () => {
    expect(isApiPath('/apidocs')).toBe(false)
    expect(isApiPath('/assets/api-DEADBEEF.js')).toBe(false)
  })
})

describe('mayCache — the one gate before any cache read or write', () => {
  it('REFUSES every /api path', () => {
    for (const path of [
      '/api',
      '/api/approvals',
      '/api/dashboard/config',
      '/api/sessions/abc',
      '/api/files?path=/etc/passwd',
    ]) {
      expect(mayCache(url(path), ORIGIN), path).toBe(false)
    }
  })

  it('refuses cross-origin responses', () => {
    expect(mayCache(url('/assets/index.js', 'https://cdn.example.com'), ORIGIN)).toBe(false)
  })

  it('refuses an unrecognised same-origin path — the default is fail-closed', () => {
    expect(mayCache(url('/some/future/route'), ORIGIN)).toBe(false)
    expect(mayCache(url('/login'), ORIGIN)).toBe(false)
    expect(mayCache(url('/mcp'), ORIGIN)).toBe(false)
  })

  it('allows the shell and the immutable build-output prefixes', () => {
    for (const entry of APP_SHELL) expect(mayCache(url(entry), ORIGIN), entry).toBe(true)
    for (const prefix of CACHEABLE_PREFIXES) {
      expect(mayCache(url(`${prefix}anything-HASH.js`), ORIGIN), prefix).toBe(true)
    }
  })
})

describe('strategyFor', () => {
  it('gives every /api request network-only — never read, never written', () => {
    for (const path of ['/api/approvals', '/api/status', '/api/inbox']) {
      expect(strategyFor(url(path), ORIGIN, false), path).toBe('network-only')
    }
  })

  it('never caches an /api request even when the browser calls it a navigation', () => {
    expect(mayCache(url('/api/approvals'), ORIGIN)).toBe(false)
  })

  it('serves navigations network-first so a deploy cannot be pinned', () => {
    expect(strategyFor(url('/'), ORIGIN, true)).toBe('network-first')
    expect(strategyFor(url('/#/companion'), ORIGIN, true)).toBe('network-first')
  })

  it('serves content-hashed assets cache-first', () => {
    expect(strategyFor(url('/assets/index-DR9ii6_w.js'), ORIGIN, false)).toBe('cache-first')
    expect(strategyFor(url('/icons/icon-192.png'), ORIGIN, false)).toBe('cache-first')
  })

  it('falls through to network-only for anything the gate rejects', () => {
    expect(strategyFor(url('/some/future/route'), ORIGIN, false)).toBe('network-only')
  })
})

describe('assistant shell policy', () => {
  it('separates assistant document fallback from API and other applications', () => {
    expect(shellDocument(url('/assistant/chat'), ORIGIN)).toBe('/assistant/')
    expect(shellDocument(url('/'), ORIGIN)).toBe('/')
    for (const path of ['/api/auth/session', '/assistant/api/auth/session', '/apps/private', '/assistant/assets/missing.js']) {
      expect(strategyFor(url(path), ORIGIN, true)).toBe('network-only')
      expect(shellDocument(url(path), ORIGIN)).toBeUndefined()
    }
  })
  it('refuses HTML returned for a missing asset and accepts the correct MIME', () => {
    expect(cacheableResponse(url('/assistant/assets/missing.js'), new Response('<html/>', { headers: { 'content-type': 'text/html' } }), ORIGIN)).toBe(false)
    expect(cacheableResponse(url('/assistant/assets/entry.js'), new Response('export {}', { headers: { 'content-type': 'text/javascript' } }), ORIGIN)).toBe(true)
    expect(mayCache(url('/assistant/assets/entry.js?token=private'), ORIGIN)).toBe(false)
    expect(cacheableResponse(url('/manifest.webmanifest'), new Response('{}', { headers: { 'content-type': 'application/manifest+json' } }), ORIGIN)).toBe(true)
  })
})
