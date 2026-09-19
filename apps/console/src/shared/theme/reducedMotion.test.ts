import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import * as motion from './motion'
import { motionRegistry, registeredMotionFamilies } from './motionRegistry'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

function setReducedMotion(matches: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ((query: string) => ({
      matches: matches && query === '(prefers-reduced-motion: reduce)',
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia,
  })
}

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ORIGINAL_MATCH_MEDIA,
  })
})

function resolve(value: unknown): unknown[] {
  if (typeof value === 'function') return resolve((value as () => unknown)())
  if (value === null || typeof value !== 'object') return []
  return [value, ...Object.values(value as Record<string, unknown>).flatMap(resolve)]
}

function registeredValues(): { family: string; value: unknown }[] {
  const values: { family: string; value: unknown }[] = []
  for (const name of Object.keys(motionRegistry.spring)) {
    values.push({ family: `spring.${name}`, value: motion.spring[name as keyof typeof motion.spring] })
  }
  for (const name of Object.keys(motionRegistry.physics)) {
    values.push({ family: `physics.${name}`, value: motion.physics[name as keyof typeof motion.physics] })
  }
  for (const name of Object.keys(motionRegistry.variants)) {
    values.push({ family: name, value: motion[name as keyof typeof motion] })
  }
  return values
}

describe('reduced motion policy', () => {
  it('covers every motion family from the registry rather than a hand-maintained test list', () => {
    expect(registeredValues().map(({ family }) => family)).toEqual(registeredMotionFamilies)
    expect(registeredMotionFamilies.length).toBeGreaterThanOrEqual(12)
  })

  it('uses the one shared preference accessor at the application boundary', () => {
    setReducedMotion(true)
    expect(motion.prefersReducedMotion()).toBe(true)
    setReducedMotion(false)
    expect(motion.prefersReducedMotion()).toBe(false)

    const app = readFileSync(join(process.cwd(), 'src/app/shell/App.tsx'), 'utf8')
    expect(app).toContain("import { ease, duration, prefersReducedMotion } from '../../shared/theme/motion'")
    expect(app).toContain("reducedMotion={reducedMotion ? 'always' : 'never'}")
    expect(app).not.toContain('reducedMotion="user"')
  })

  it('never resolves a spring or an indefinite transition when motion is reduced', () => {
    setReducedMotion(true)
    const offenders: string[] = []
    for (const { family, value } of registeredValues()) {
      for (const candidate of resolve(value)) {
        const object = candidate as Record<string, unknown>
        if (object.type === 'spring' || object.repeat === Infinity) offenders.push(family)
      }
    }
    expect(offenders).toEqual([])
  })

  it('proves the indefinite-animation assertion observes the registered pulse', () => {
    setReducedMotion(false)
    const pulse = registeredValues().find(({ family }) => family === 'thinkingPulse')
    expect(pulse).toBeDefined()
    expect(resolve(pulse!.value).some((value) => (value as Record<string, unknown>).repeat === Infinity)).toBe(true)
  })
})
