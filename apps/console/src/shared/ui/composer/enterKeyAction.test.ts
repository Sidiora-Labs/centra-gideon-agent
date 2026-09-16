import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { enterKeyAction } from './enterKeyAction'


const SRC = join(process.cwd(), "src")
const codeOf = (...p: string[]) =>
  readFileSync(join(SRC, ...p), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('Enter means what the reader asked it to mean', () => {
  it('sends by default — the shipped behaviour, unchanged', () => {
    expect(enterKeyAction({ menuOpen: false, canSend: true })).toBe('send')
  })

  it('inserts a newline when the preference is off, even with a sendable draft', () => {
    expect(enterKeyAction({ menuOpen: false, canSend: true, sendOnEnter: false })).toBe('newline')
  })

  it('🔑 keeps sending while the preference is UNKNOWN', () => {
    expect(enterKeyAction({ menuOpen: false, canSend: true, sendOnEnter: undefined })).toBe('send')
  })

  it('still inserts a newline on a phone even with the preference on', () => {
    expect(enterKeyAction({ menuOpen: false, canSend: true, mobile: true, sendOnEnter: true })).toBe('newline')
  })

  it('lets an open typeahead menu own the key, whatever the preference says', () => {
    for (const sendOnEnter of [true, false, undefined]) {
      for (const mobile of [true, false]) {
        expect(enterKeyAction({ menuOpen: true, canSend: true, mobile, sendOnEnter })).toBe('menu')
      }
    }
  })

  it('declines when there is nothing to send, so CodeMirror keeps its default', () => {
    expect(enterKeyAction({ menuOpen: false, canSend: false })).toBe('none')
    expect(enterKeyAction({ menuOpen: false, canSend: false, sendOnEnter: false })).toBe('newline')
  })

  it('never sends when the preference is off, across every other input', () => {
    for (const mobile of [true, false, undefined]) {
      for (const canSend of [true, false]) {
        expect(enterKeyAction({ menuOpen: false, mobile, canSend, sendOnEnter: false })).not.toBe('send')
      }
    }
  })
})

describe('the preference actually reaches the key', () => {

  it('the composer reads the stored preference and hands it to the editor', () => {
    const code = codeOf('shared/ui', 'Composer.tsx')
    expect(code.length, 'read Composer.tsx').toBeGreaterThan(1000)
    expect(code).toMatch(/useQuery\(\s*'chat:send-on-enter'/)
    expect(code).toMatch(/\.send_on_enter\b/)
    expect(code).toMatch(/sendOnEnter=\{sendOnEnter\}/)
  })

  it('the editor declares the prop and feeds it to the decision', () => {
    const code = codeOf('shared/ui', 'composer', 'MarkdownInput.tsx')
    expect(code.length, 'read MarkdownInput.tsx').toBeGreaterThan(1000)
    expect(code).toMatch(/sendOnEnter\?:\s*boolean/)
    expect(code).toMatch(/from '\.\/enterKeyAction'/)
    expect(code).toMatch(/sendOnEnter:\s*cb\.current\.sendOnEnter/)
    expect(code).not.toMatch(/!\s*cb\.current\.sendOnEnter/)
  })

  it('both writers bust the reader key, so the keyboard changes when the switch does', () => {
    expect(codeOf('features', 'settings', 'ChatPanel.tsx')).toMatch(/invalidateKeys\('chat:send-on-enter'\)/)
    expect(codeOf('features', 'settings', 'settingsWidgets.tsx')).toMatch(/'chat:send-on-enter'/)
  })
})

describe('the settings copy describes what the key does', () => {
  const panel = codeOf('features', 'settings', 'ChatPanel.tsx')
  const hint = panel.match(/label="Send on Enter" hint=\{([^}]*)\}/)?.[1] ?? ''

  it('found the hint — otherwise every assertion below passes vacuously', () => {
    expect(hint, 'the Send on Enter row hint expression').not.toBe('')
    expect(hint).toMatch(/\?/)
  })

  it('names both real behaviours', () => {
    expect(hint).toMatch(/Enter sends/)
    expect(hint).toMatch(/Enter inserts a newline/)
  })

  it('🪤 promises no mod-chord send while that chord optimizes the prompt', () => {
    expect(hint).not.toMatch(/(Cmd|Ctrl|⌘|Mod)[^']*Enter sends/i)

    const editor = codeOf('shared/ui', 'composer', 'MarkdownInput.tsx')
    const modEnter = editor.match(/key: 'Mod-Enter', run:([\s\S]*?)(?=\{ key: '|\]\))/)?.[1] ?? ''
    expect(modEnter, 'the Mod-Enter binding').not.toBe('')
    expect(modEnter).toMatch(/onOptimize/)
    expect(modEnter).not.toMatch(/onSend/)
  })
})
