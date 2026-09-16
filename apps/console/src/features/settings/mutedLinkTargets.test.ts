import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const voice = readFileSync(join(SRC, 'features/settings/VoicePanel.tsx'), 'utf8')
const design = readFileSync(join(SRC, 'features/settings/DesignPanel.tsx'), 'utf8')
const codeOf = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the voice row converges on the link it already had', () => {
  it('the Providers link is a TextLink now', () => {
    const code = codeOf(voice)
    const link = /<TextLink onClick=\{\(\) => go\('providers'\)\}[^>]*>/.exec(code)?.[0] ?? ''
    expect(link, 'the Providers link is a TextLink').toBeTruthy()
    for (const prop of ['icon={ArrowRight}', 'iconPosition="trailing"', 'size="xs"', 'ink="emphasis"'])
      expect(link, `carries ${prop}`).toContain(prop)
  })

  it('no hand-rolled muted twin remains', () => {
    const code = codeOf(voice)
    expect(code, 'the 18px hand-rolled button must be gone')
      .not.toMatch(/inline-flex items-center gap-1 text-\[0\.75rem\] text-on-surface-low hover:text-on-surface hover:underline/)
  })

  it('both links in the row now use the same primitive with the same props', () => {
    const code = codeOf(voice)
    const links = [...code.matchAll(/<TextLink[\s\S]{0,300}?<\/TextLink>/g)]
      .filter((m) => /icon=\{ArrowRight\} iconPosition="trailing" size="xs"/.test(m[0]))
    expect(links.length, 'the Models link and the Providers link').toBe(2)
  })
})

describe('the design disclosure keeps its tone and takes the geometry', () => {
  it('grew by padding that is handed straight back', () => {
    expect(codeOf(design)).toMatch(/data-type="body-s" className="flex items-center gap-s py-1 -my-1 text-on-surface-var"/)
  })

  it('did NOT become a coral TextLink', () => {
    const code = codeOf(design)
    const at = code.indexOf('Edit colors')
    const around = code.slice(Math.max(0, at - 400), at)
    expect(around, 'a disclosure is not a navigation').not.toMatch(/<TextLink/)
    expect(around).toMatch(/text-on-surface-var/)
  })

  it('is still a disclosure — the rotating chevron and the toggle both survive', () => {
    const code = codeOf(design)
    expect(code).toMatch(/setEditingColors\(\(v\) => !v\)/)
    expect(code).toMatch(/rotate-180/)
  })
})
