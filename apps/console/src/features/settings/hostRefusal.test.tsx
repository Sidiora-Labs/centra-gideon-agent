import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { hostRefusal } from './SecurityPanel'


const PANEL = join(process.cwd(), "src/features/settings/SecurityPanel.tsx")
const read = () => readFileSync(PANEL, 'utf8')

describe('a refused host says why', () => {
  it('a pasted URL is refused with the rule, not silence', () => {
    for (const bad of ['https://nas.local', 'http://nas.local/path', 'nas.local:8080', 'two words']) {
      const why = hostRefusal(bad, [])
      expect(why, `${bad} must be refused`).toBeTruthy()
      expect(why, `${bad}: the message must state the rule`).toMatch(/bare hostname/)
      expect(why, 'and show the shape that works').toMatch(/nas\.local/)
    }
  })

  it('a duplicate names the host that is already there', () => {
    expect(hostRefusal('nas.local', ['nas.local'])).toBe('nas.local is already listed.')
    expect(hostRefusal('  NAS.Local  ', ['nas.local'])).toBe('nas.local is already listed.')
  })

  it('a valid host is not refused', () => {
    expect(hostRefusal('nas.local', [])).toBeNull()
    expect(hostRefusal('printer.lan', ['nas.local'])).toBeNull()
    expect(hostRefusal('   ', [])).toBeNull()
  })

  it('the refusal is ANNOUNCED, not merely rendered', () => {
    const src = read().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'the add path must render through FieldError').toMatch(/\{refused && <FieldError>\{refused\}<\/FieldError>\}/)
    expect(src, 'a hand-rolled danger line would be silent — that is this family\'s defect')
      .not.toMatch(/refused && <p className="text-danger/)
    expect(src, 'and the input is marked invalid while it stands').toMatch(/aria-invalid=\{refused \? true : undefined\}/)
  })

  it('a refusal KEEPS the draft, and typing clears the refusal', () => {
    const src = read().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'the duplicate branch must not clear the draft').not.toMatch(/hosts\.includes\(h\)\) \{ setDraft\(''\)/)
    expect(src, 'a refusal sets the message and returns').toMatch(/if \(why\) \{ setRefused\(why\); return \}/)
    expect(src, 'a successful add clears both').toMatch(/setRefused\(''\); onChange\(\[\.\.\.hosts, h\]\); setDraft\(''\)/)
    expect(src, 'and editing the text dismisses the stale refusal').toMatch(/if \(refused\) setRefused\(''\)/)
  })

  it('both lists get this — the component renders twice', () => {
    const src = read()
    expect((src.match(/<HostList\b/g) || []).length, 'allow + deny').toBe(2)
    expect((src.match(/function HostList\b/g) || []).length, 'one implementation, so one fix').toBe(1)
  })
})
