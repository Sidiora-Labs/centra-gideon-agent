import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const PANEL = join(process.cwd(), "src/features/settings/AccountPanel.tsx")
const read = () => readFileSync(PANEL, 'utf8')
const stripped = () => read().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('every Save on the account panel says what it saves', () => {
  it('the three identity buttons carry distinct names', () => {
    const src = stripped()
    for (const name of ['Save: Your name', 'Save: Username', 'Save: Assistant name']) {
      expect(src, `${name} must be the accessible name of one button`).toContain(name)
    }
    const names = [...src.matchAll(/aria-?[Ll]abel="(Save: [^"]+)"/g)].map((m) => m[1])
    expect(names.length, 'all three named').toBe(3)
    expect(new Set(names).size, 'and no two share a name').toBe(3)
  })

  it('the visible word is still "Save" — a name fix, not a relabel', () => {
    const src = stripped()
    expect((src.match(/\? 'Saved' : 'Save'\}/g) || []).length, 'the three visible labels are untouched').toBe(3)
  })

  it('camelCase on ui/Button, dashed on a raw button — and never the other way round', () => {
    const src = stripped()
    expect(src, 'the kit button takes ariaLabel').toMatch(/<Button[^>]*ariaLabel="Save: Username"/)
    expect(src, 'and no dashed aria-label may appear on a <Button')
      .not.toMatch(/<Button[^>]*aria-label=/)
  })
})

describe('the sign-in username is committed by a button that exists', () => {
  it('it lives in the credential form, not a field of its own', () => {
    const src = stripped()
    expect(src, 'the standalone Field must be gone').not.toMatch(/<Field label="Sign-in username"/)
    expect(src, 'and the input sits in the credential Field with its own name')
      .toMatch(/<TextInput value=\{userDraft\} onChange=\{setUserDraft\} placeholder="you" ariaLabel="Sign-in username" \/>/)
  })

  it('the label and hint state that both are saved together', () => {
    const src = stripped()
    expect(src, 'the label names both halves').toMatch(/Set a sign-in username and password/)
    expect(src, 'and the change case too').toMatch(/Change the sign-in username or password/)
    expect(src, 'the hint says why a username alone cannot go').toMatch(/saved together, in one step/)
  })

  it('the button answers "why can\'t I save just the username?"', () => {
    const src = stripped()
    expect(src, 'the dirty-username branch must exist').toMatch(/userDirty \? 'Enter the password too — the username is saved with it'/)
    expect(src, 'and it is computed from the server value, not a guess')
      .toMatch(/const userDirty = userDraft\.trim\(\) !== \(state\.username \|\| ''\)/)
    expect(src, 'the button names what it commits').toMatch(/'Saved' : 'Save sign-in'/)
    expect(src, 'the old password-only label is gone').not.toMatch(/'Saved' : 'Save password'/)
  })

  it('nothing here invents a username-only writer', () => {
    const src = stripped()
    expect(src, 'still exactly one credential writer').toMatch(/api\.setLoginPassword\(userDraft\.trim\(\), pwDraft\)/)
    expect((src.match(/setLoginPassword\(/g) || []).length, 'and only one call site').toBe(1)
  })
})
