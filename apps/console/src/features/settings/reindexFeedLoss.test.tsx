import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('a dropped re-index feed reports itself honestly', () => {
  it('the stream handler records the loss instead of closing in silence', () => {
    const code = read('features/settings/ModelsPanel.tsx')
    expect(code, 'it still closes the dead stream').toMatch(/es\.onerror = \(\) => \{\s*\n\s*es\.close\(\)/)
    expect(code, 'and records it on the job the panel already renders')
      .toMatch(/status: 'error', error: 'Lost the progress feed/)
  })

  it('the copy does not claim the re-index failed', () => {
    const code = read('features/settings/ModelsPanel.tsx')
    expect(code).toMatch(/may still be running in the background/)
    expect(code, 'never asserts the job stopped').not.toMatch(/error: 'Re-index failed/)
  })

  it('it only overwrites a RUNNING job, so a finished one is never relabelled', () => {
    expect(read('features/settings/ModelsPanel.tsx')).toMatch(/r && r\.status === 'running'/)
  })

  it('"Re-index not started" is now conditional on the job never having started', () => {
    const code = read('features/settings/ModelsPanel.tsx')
    expect(code).toMatch(/\{reindex\.id \? reindex\.error : `Re-index not started: \$\{reindex\.error\}`\}/)
  })

  it('the not-started path that the prefix belongs to still sets an empty id', () => {
    expect(read('features/settings/ModelsPanel.tsx')).toMatch(/setReindex\(\{ id: '', model: '', status: 'error'/)
  })
})

describe('the re-index bar reports only progress it can compute', () => {
  it('is determinate ONLY when there is a denominator', () => {
    const code = read('features/settings/ModelsPanel.tsx')
    expect(code, 'the determinate track is gated on a real total').toMatch(
      /\{reindex\.total > 0 \? \([\s\S]{0,400}?rounded-pill bg-primary transition-\[width\]/,
    )
  })

  it('falls back to the indeterminate wave, not a fabricated fill', () => {
    const code = read('features/settings/ModelsPanel.tsx')
    expect(code, 'the no-total branch renders the indeterminate wave').toMatch(/<WavyProgress width=\{\d+\} \/>/)
    expect(code, 'the wave must not carry a value').not.toMatch(/<WavyProgress[^>]*\bvalue=/)
  })

  it("never pins the fill to a literal percentage", () => {
    const code = read('features/settings/ModelsPanel.tsx')
    expect(code, "the hardcoded '40%' fill must stay gone").not.toMatch(/: '40%'/)
    expect(code, 'and no other literal percentage may drive a fill width')
      .not.toMatch(/width: [^,}\n]*['"](?!0%|100%)\d+(?:\.\d+)?%['"]/)
  })
})
