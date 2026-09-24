import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

describe('first-run scroll container', () => {
  const source = readFileSync('src/app/shell/Onboarding.tsx', 'utf8')
  it('bounds the scroll surface to the dynamic viewport rather than the document', () => {
    const root = source.match(/<div data-onboarding-scroll className="([^"]+)"/)?.[1]
    expect(root).toBeTruthy()
    expect(root?.split(' ')).toEqual(expect.arrayContaining(['h-dvh', 'min-h-0', 'overflow-y-auto', 'overscroll-contain']))
  })
  it('starts tall content at the reachable top while permitting the main content to grow', () => {
    const main = source.match(/<main className="([^"]+)"/)?.[1]
    expect(main).toContain('min-h-full')
    expect(main).toContain('justify-start')
    expect(main).not.toContain('justify-center')
  })
})
