import { afterEach, expect, it } from 'vitest'
import * as monaco from 'monaco-editor/editor/editor.api'
import { ApprovalNotifications } from './approvalNotifications'
import { workerFamily, tomlLanguage } from './editorLanguages'
import { isDisclosed, onNavDisclosureChange, pinNavSurface, readNavDisclosure, setNavMode } from './navDisclosure'
import type { WsMessage } from '../../shared/data/useChatSocket'

const approval = (id: string, overrides: Record<string, unknown> = {}): WsMessage => ({ type: 'approval', data: { id, session: 'other', tool: 'Read', ...overrides } })
afterEach(() => localStorage.clear())
it('approval notices follow the active session without consuming an unseen notification', () => {
  const notices = new ApprovalNotifications()
  expect(notices.receive(approval('one'), 'other')).toBeUndefined()
  expect(notices.receive(approval('one'), '')).toContain('Another chat session needs approval')
  expect(notices.receive(approval('one'), '')).toBeUndefined()
  expect(notices.receive(approval('two', { source: 'subagent' }), '')).toContain('A subagent')
  expect(notices.receive(approval('three', { source: 'schedule' }), '')).toContain('A background task')
})
it('approval history stays bounded while keeping the most recent replay guards', () => {
  const notices = new ApprovalNotifications()
  for (let id = 0; id <= 200; id += 1) expect(notices.receive(approval(String(id)), '')).toBeDefined()
  expect(notices.receive(approval('200'), '')).toBeUndefined()
  expect(notices.receive(approval('0'), '')).toBeDefined()
  expect(notices.receive(approval('', { session: '' }), '')).toBeUndefined()
})
it('disclosure notifications cover local writes and matching storage events and detach', () => {
  let changes = 0
  const unsubscribe = onNavDisclosureChange(() => { changes += 1 })
  setNavMode('starter')
  pinNavSurface('tools')
  pinNavSurface('tools')
  window.dispatchEvent(new StorageEvent('storage', { key: 'mode' }))
  window.dispatchEvent(new StorageEvent('storage', { key: 'nav-disclosure' }))
  expect(changes).toBe(3)
  expect(readNavDisclosure()).toEqual({ mode: 'starter', pinned: ['tools'] })
  expect(isDisclosed('app/weather', 'starter', [])).toBe(true)
  unsubscribe()
  setNavMode('expert')
  expect(changes).toBe(3)
})
it('maps supported editor languages to their bundled workers with a base fallback', () => {
  const families = { json: ['json'], css: ['css', 'scss', 'less'], html: ['html', 'handlebars', 'razor'], typescript: ['typescript', 'javascript'], editor: ['toml', 'python', 'unknown'] }
  for (const [family, labels] of Object.entries(families)) for (const label of labels) expect(workerFamily(label)).toBe(family)
})
it('tokenizes TOML arrays, numeric bases, escaped strings and multiline values using Monaco', () => {
  const id = 'gideon-test-toml'
  monaco.languages.register({ id })
  const provider = monaco.languages.setMonarchTokensProvider(id, tomlLanguage)
  try {
    const source = '[[agents]]\nhex = 0xff\nname = "a\\"b" # note\ntext = """hello\nworld"""'
    const tokens = monaco.editor.tokenize(source, id)
    expect(tokens[0].some((token) => token.type.startsWith('type.identifier'))).toBe(true)
    expect(tokens[1].some((token) => token.type.startsWith('number'))).toBe(true)
    expect(tokens[2].some((token) => token.type.startsWith('string.escape'))).toBe(true)
    expect(tokens[2].some((token) => token.type.startsWith('comment'))).toBe(true)
    expect(tokens[4].some((token) => token.type.startsWith('string'))).toBe(true)
  } finally { provider.dispose() }
})
