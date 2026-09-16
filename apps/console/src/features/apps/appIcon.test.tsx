import { describe, expect, it } from 'vitest'
import { renderToString } from 'react-dom/server'
import { createElement } from 'react'
import * as Lucide from 'lucide-react'
import { Blocks, icons as lucideIconRegistry } from 'lucide-react'
import { resolveAppIcon } from './appIcon'

describe('resolveAppIcon', () => {
  const letterStarting = Object.keys(Lucide).filter((n) => /^[A-Za-z]/.test(n))

  it('resolves every lucide export to something React can actually render', () => {
    expect(letterStarting.length).toBeGreaterThan(5000)

    const crashed: Array<[string, string]> = []
    for (const name of letterStarting) {
      try {
        renderToString(createElement(resolveAppIcon(name), { size: 18 }))
      } catch (e) {
        crashed.push([name, (e as Error).message.slice(0, 80)])
      }
    }
    expect(crashed).toEqual([])
  })

  it('still resolves real icons rather than falling everything back to Blocks', () => {
    const resolved = letterStarting.filter((n) => resolveAppIcon(n) !== Blocks)
    expect(resolved.length).toBeGreaterThan(5000)
  })

  it('rejects the container exports that are truthy but not components', () => {
    expect(resolveAppIcon('icons')).toBe(Blocks)
    expect(resolveAppIcon('default')).toBe(Blocks)
  })

  it('rejects exports that are VALID components but still throw when rendered', () => {
    expect(resolveAppIcon('Icon')).toBe(Blocks)
    expect(resolveAppIcon('createLucideIcon')).toBe(Blocks)
    expect(resolveAppIcon('useLucideContext')).toBe(Blocks)
  })

  it('keeps ALIAS names working, not just the registry’s canonical keys', () => {
    const canonical = Object.keys(lucideIconRegistry)
    for (const alias of ['SquareTerminalIcon', 'LucideSquareTerminal', 'AlarmCheck']) {
      expect(canonical, `${alias} must be an ALIAS for this test to mean anything`).not.toContain(alias)
      expect(resolveAppIcon(alias), `${alias} is a valid lucide name and must resolve`).not.toBe(Blocks)
    }
    expect(resolveAppIcon('SquareTerminalIcon')).toBe(resolveAppIcon('SquareTerminal'))
  })

  it('falls back for absent, non-letter and unknown names', () => {
    expect(resolveAppIcon(undefined)).toBe(Blocks)
    expect(resolveAppIcon('')).toBe(Blocks)
    expect(resolveAppIcon('\u{1F389}')).toBe(Blocks)
    expect(resolveAppIcon('NotArealIcon')).toBe(Blocks)
  })
})
