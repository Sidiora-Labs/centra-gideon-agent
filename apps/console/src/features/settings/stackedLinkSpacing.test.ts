import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const voicePanel = () => readFileSync(join(SRC, 'features/settings/VoicePanel.tsx'), 'utf8')
const textLink = () => readFileSync(join(SRC, 'shared/ui/TextLink.tsx'), 'utf8')

describe('ManageLink stacks its links without overlapping them', () => {
  it('the wrapping row leaves more vertical gap than TextLink bleeds', () => {
    const src = voicePanel()
    const row = src.match(/<div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-\d+">/)?.[0] ?? ''
    expect(row, 'the ManageLink row must exist').toContain('flex-wrap')
    const gap = Number(row.match(/gap-y-(\d+)/)?.[1] ?? 0)
    expect(gap * 4, `gap-y-${gap} = ${gap * 4}px must exceed TextLink's 8px of -my-1 bleed`).toBeGreaterThan(8)
  })

  it('the bleed this guards against is still real', () => {
    expect(textLink(), 'TextLink still grows its hit box with py-1 -my-1').toMatch(/py-1 -my-1/)
  })

  it('the exact pre-fix value does not come back', () => {
    expect(/gap-x-4 gap-y-1"/.test(voicePanel()), 'gap-y-1 reintroduces the -4px overlap').toBe(false)
  })

  it('both links still route through the shared primitive', () => {
    const src = voicePanel()
    const start = src.indexOf('function ManageLink')
    const next = src.indexOf('\nfunction ', start + 1)
    const row = src.slice(start, next === -1 ? undefined : next)
    expect((row.match(/<TextLink/g) || []).length, 'both links are TextLinks').toBe(2)
    expect(row).toMatch(/size="xs"/)
  })
})
