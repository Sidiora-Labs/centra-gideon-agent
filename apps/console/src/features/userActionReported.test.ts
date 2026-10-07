import { apiCalls, callOwner, callStatement, namedOwner } from '../shared/testing/sourceOwners'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const F = (rel: string) => readFileSync(join(process.cwd(), "src", rel), 'utf8')
const strip = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const FIXED: Array<[string, string, string]> = [
  ['features/loop/LoopComposer.tsx', 'fileUpload', 'upload '],
  ['features/ChatPage.tsx', 'renameSession', 'rename this chat'],
  ['features/ChatPage.tsx', 'setSessionLifecycle', 'this chat'],
  ['features/ChatPage.tsx', 'createChatFolder', 'create the folder'],
  ['features/agents/agentLibraryState.ts', 'setDefaultAgent', 'the default agent'],
  ['features/tools/ToolsPage.tsx', 'probeMcp', 're-probe the MCP servers'],
  ['features/dashboard/PinnedTiles.tsx', 'refreshTile', 'refresh this tile'],
  ['features/inbox/inboxQueueState.ts', 'restartInbox', 'restart the inbox sources'],
  ['features/inbox/inboxQueueState.ts', 'digestInboxChannel', 'generate the digest'],
  ['features/agents/agentLibraryState.ts', 'syncAgents', 'sync the agents'],
  ['features/notifications/notificationFeedState.ts', 'deleteNotification', 'delete this notification'],
  ['features/knowledge/KnowledgeListPage.tsx', 'deleteKnowledgeIntent', 'delete this intent'],
]

describe('a user-initiated write that fails tells the user', () => {
  it('none of the twelve swallows — the ratchet, keyed on the WRITES', () => {
    const offenders: string[] = []
    for (const [rel, call] of FIXED) {
      const scan = strip(F(rel))
      const found = apiCalls(scan, call)
      expect(found.length, `${rel}: api.${call} must still be called`).toBeGreaterThan(0)
      for (const node of found) {
        const owner = callOwner(node)
        if (call === 'refreshTile' && !/force: true/.test(node.getText())) continue
        if (/\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)/.test(owner)) offenders.push(`${rel}:${call}`)
      }
    }
    expect(offenders, 'a person clicked; silence is not an answer').toEqual([])
  })

  it('each reports through the shared contract and names its subject', () => {
    for (const [rel, call, phrase] of FIXED) {
      const src = strip(F(rel))
      if (call !== 'deleteNotification') expect(src, `${rel} must import the shared reporter`).toMatch(
        /import \{[^}]*report(ingWrite|ActionFailure)[^}]*\} from '[^']*app\/shell\/reportingWrite'/,
      )
      const calls = apiCalls(src, call).filter(node => call !== 'refreshTile' || /force: true/.test(node.getText()))
      expect(calls.length).toBeGreaterThan(0)
      for (const node of calls) {
        const owner = callOwner(node)
        if (call === 'deleteNotification') {
          expect(owner).toContain("notify(`Couldn't delete this notification: ${describeFailure(error)}`, 'error')")
          expect(owner).toContain('return false')
        } else {
          expect(owner, `${rel}: api.${call} must be reported by its actual owner`).toMatch(/report(ingWrite|ActionFailure)\(/)
          expect(owner).toContain(phrase)
        }
      }
      expect(src, `${rel}: the message must name its subject (${phrase})`).toContain(phrase)
    }
  })

  it('THE POLL still swallows — reporting it would be the defect', () => {
    const src = strip(F('features/dashboard/PinnedTiles.tsx')).replace(/=>/g, '⇒')
    const at = src.indexOf('const tick = useCallback(')
    expect(at, 'the poll must exist').toBeGreaterThan(-1)
    const tick = src.slice(at, src.indexOf('useVisiblePoll', at))
    expect(tick, 'the poll must not report').toMatch(/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/)
    expect(tick, 'and it is the un-forced call').not.toContain('force: true')
  })

  it('the VIEW-RENDER side effect still swallows — its own comment rules on it', () => {
    const raw = F('features/artifacts/ArtifactViewer.tsx')
    const calls = apiCalls(raw, 'viewRender')
    expect(calls).toHaveLength(1)
    const statement = callStatement(calls[0])
    expect(statement).toMatch(/\.catch\(\(\) => \{\}\)/)
    expect(statement).not.toMatch(/reportActionFailure|reportingWrite/)
    expect(callOwner(calls[0])).toContain('useEffect(() => { api.viewRender')
  })

  it('the three GATING decisions are each what they should be', () => {
    const chat = strip(F('features/ChatPage.tsx'))

    const folder = namedOwner(chat, 'createFolder')
    expect(folder, 'createChatFolder gates its reload').toMatch(/\)\)\) return/)
    const gate = folder.indexOf(')) return')
    expect(gate, 'and the guard precedes it').toBeLessThan(folder.indexOf('load()', gate))

    const life = namedOwner(chat, 'setLifecycle')
    expect(life, 'the optimistic move is still there').toContain('setSessions((prev)')
    expect(life, 'the repair refetch still runs').toContain('load()')
    expect(life, 'and must NOT be gated — that would leave the row lying').not.toMatch(/\)\)\) return/)

    const composer = strip(F('features/loop/LoopComposer.tsx'))
    const submit = namedOwner(composer, 'submit')
    const upload = apiCalls(submit, 'fileUpload')
    expect(upload).toHaveLength(1)
    expect(upload[0].parent.getText()).not.toMatch(/\breturn\b/)
    expect(submit.indexOf('onCreated(loop, (cls.intake_rigor')).toBeGreaterThan(submit.indexOf('api.fileUpload('))
  })

  it('the optimistic rename reports rather than reverting', () => {
    const chat = strip(F('features/ChatPage.tsx'))
    const calls = apiCalls(chat, 'renameSession')
    expect(calls).toHaveLength(1)
    const owner = callOwner(calls[0])
    expect(owner).toContain('setTitle(v)')
    expect(owner).not.toMatch(/setTitle\(title\)|setTitle\(prev/)
  })

  it('a failed rewind reports as an error, not as something the assistant said', () => {
    const chat = strip(F('features/ChatPage.tsx'))
    const fn = namedOwner(chat, 'rewindToTurn')
    expect(fn, 'the failure names its subject through the shared reporter')
      .toMatch(/reportActionFailure\(`rewind to turn/)
    expect(fn, 'the transcript no longer carries the failure').not.toContain('Rewind failed')
    expect(fn, 'no catch appends an assistant turn').not.toMatch(/catch[\s\S]{0,200}assistantTurn\(/)
    expect(fn.split('assistantTurn(').length - 1, 'both success notices remain').toBe(2)
  })
})
