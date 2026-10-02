import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { REMOTE_CAPABILITIES, validateRemoteEndpoint } from './RemoteAccess'

describe('Hypermid remote access', () => {
  it('accepts only explicit TLS transport endpoints with host and port', () => {
    expect(validateRemoteEndpoint('tcp://hypermid.example.net:443')).toBe('')
    expect(validateRemoteEndpoint('http://hypermid.example.net:443')).toContain('never downgrades')
    expect(validateRemoteEndpoint('tcp://hypermid.example.net')).toContain('hostname and port')
    expect(validateRemoteEndpoint('tcp://operator:secret@hypermid.example.net:443')).toContain('credentials')
  })

  it('offers a bounded read-only capability set', () => {
    const identifiers = REMOTE_CAPABILITIES.map(({ id }) => id)
    expect(new Set(identifiers).size).toBe(identifiers.length)
    expect(identifiers).toEqual([
      'sessions.inspect',
      'memory.list',
      'memory.inspect',
      'cache.list',
      'config.read',
      'maintenance.status',
    ])
    expect(identifiers.some((id) => /writer|approval|tool|final/.test(id))).toBe(false)
  })

  it('keeps the native surface operable at narrow widths and reduced motion', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/features/hypermid/hypermid.css'), 'utf8')
    expect(css).toMatch(/overflow-x:\s*clip/)
    expect(css).toMatch(/min-height:\s*44px/)
    expect(css).toMatch(/:focus-visible[\s\S]*outline:\s*2px solid var\(--color-primary\)/)
    expect(css).toMatch(/@media \(max-width:\s*390px\)/)
    expect(css).toMatch(/grid-template-columns:\s*minmax\(0, 1fr\)/)
    expect(css).toMatch(/@media \(prefers-reduced-motion:\s*reduce\)/)
  })
})
