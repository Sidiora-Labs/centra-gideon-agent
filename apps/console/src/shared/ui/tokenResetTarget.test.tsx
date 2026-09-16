import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const code = readFileSync(join(SRC, 'shared/ui/TokenControls.tsx'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the shared token reset is a 24px target with its own name', () => {
  it('clicks 24×24', () => {
    expect(code).toMatch(/className="grid size-6 -mx-1 place-items-center text-on-surface-low/)
  })

  it('hands most of the width back, so the row does not reflow vertically', () => {
    expect(code).toMatch(/-mx-1/)
  })

  it('keeps the 15px glyph — the fix is the hit box, not the design', () => {
    expect(code).toMatch(/<RotateCcw size=\{15\} strokeWidth=\{2\} \/>/)
  })

  it('names WHICH token it resets', () => {
    expect(code).toMatch(/title=\{`Reset \$\{label\}`\}/)
    expect(code, 'the ambiguous shared name must be gone').not.toMatch(/title="Reset"/)
  })

  it('every adopter passes the label — all three row kinds', () => {
    const passes = [...code.matchAll(/<TokenRow label=\{token\.label\}/g)]
    expect(passes.length, 'ColorControl, SelectControl and ScalarControl').toBe(3)
    expect(code).toMatch(/<ResetButton label=\{label\} onReset=\{onReset\}/)
  })

  it('the label is required, so a future adopter cannot forget it', () => {
    expect(code).toMatch(/function ResetButton\(\{ onReset, label \}: \{ onReset: \(\) => void; label: string \}\)/)
  })

  it('keeps the spin microinteraction', () => {
    expect(code).toMatch(/setRotation\(\(angle\) => angle - 360\)/)
    expect(code).toMatch(/animate=\{\{ rotate: reduced \? 0 : rotation \}\}/)
  })
})
