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
  ['features/agents/AgentsListPage.tsx', 'setDefaultAgent', 'the default agent'],
  ['features/tools/ToolsPage.tsx', 'probeMcp', 're-probe the MCP servers'],
  ['features/dashboard/PinnedTiles.tsx', 'refreshTile', 'refresh this tile'],
  ['features/inbox/InboxPage.tsx', 'restartInbox', 'restart the inbox sources'],
  ['features/inbox/InboxPage.tsx', 'digestInboxChannel', 'generate the digest'],
  ['features/agents/AgentsListPage.tsx', 'syncAgents', 'sync the agents'],
  ['features/notifications/NotificationsPage.tsx', 'deleteNotification', 'delete this notification'],
  ['features/knowledge/KnowledgeListPage.tsx', 'deleteKnowledgeIntent', 'delete this intent'],
]

describe('a user-initiated write that fails tells the user', () => {
  it('none of the twelve swallows — the ratchet, keyed on the WRITES', () => {
    const offenders: string[] = []
    for (const [rel, call] of FIXED) {
      const scan = strip(F(rel)).replace(/=>/g, '⇒')
      const found = [...scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))]
      expect(found.length, `${rel}: api.${call} must still be called`).toBeGreaterThan(0)
      for (const m of found) {
        const chain = scan.slice(m.index!, m.index! + 240)
        if (call === 'refreshTile' && !/force: true/.test(chain)) continue
        if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(chain)) offenders.push(`${rel}:${call}`)
      }
    }
    expect(offenders, 'a person clicked; silence is not an answer').toEqual([])
  })

  it('each reports through the shared contract and names its subject', () => {
    for (const [rel, call, phrase] of FIXED) {
      const src = strip(F(rel))
      expect(src, `${rel} must import the shared reporter`).toMatch(
        /import \{[^}]*report(ingWrite|ActionFailure)[^}]*\} from '[^']*app\/reportingWrite'/,
      )
      const occurrences = [...src.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))].map((m) => m.index!)
      const at = call === 'refreshTile'
        ? occurrences.find((i) => /force: true/.test(src.slice(i, i + 120)))!
        : occurrences[0]
      expect(at, `${rel}: the covered api.${call} call must exist`).toBeGreaterThan(-1)
      const around = src.slice(Math.max(0, at - 260), at + 240)
      expect(around, `${rel}: api.${call} must be reported`).toMatch(/report(ingWrite|ActionFailure)\(/)
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
    const flat = raw.replace(/\n\s*\/\/\s*/g, ' ')
    expect(flat, 'the ruling must stay recorded next to the code').toMatch(
      /must never block the open or surface an error toast/,
    )
    const scan = strip(raw).replace(/=>/g, '⇒')
    const at = scan.indexOf('api.viewRender(')
    expect(at).toBeGreaterThan(-1)
    expect(scan.slice(at, at + 160)).toMatch(/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/)
  })

  it('the three GATING decisions are each what they should be', () => {
    const chat = strip(F('features/ChatPage.tsx'))

    const folder = chat.slice(chat.indexOf('async function createFolder()'), chat.indexOf('async function createFolder()') + 520)
    expect(folder, 'createChatFolder gates its reload').toMatch(/\)\)\) return/)
    const gate = folder.indexOf(')) return')
    expect(gate, 'and the guard precedes it').toBeLessThan(folder.indexOf('load()', gate))

    const life = chat.slice(chat.indexOf('async function setLifecycle('), chat.indexOf('async function setNeverArchive('))
    expect(life, 'the optimistic move is still there').toContain('setSessions((prev)')
    expect(life, 'the repair refetch still runs').toContain('load()')
    expect(life, 'and must NOT be gated — that would leave the row lying').not.toMatch(/\)\)\) return/)

    const composer = strip(F('features/loop/LoopComposer.tsx'))
    const up = composer.slice(composer.indexOf('api.fileUpload('), composer.indexOf('onCreated(loop.id'))
    expect(up, 'a failed upload must not block the launch').not.toMatch(/\breturn\b/)
  })

  it('the optimistic rename reports rather than reverting', () => {
    const chat = strip(F('features/ChatPage.tsx'))
    const at = chat.indexOf('api.renameSession(')
    const around = chat.slice(Math.max(0, at - 200), at + 200)
    expect(around, 'the optimistic set stays').toContain('setTitle(v)')
    expect(around, 'and no revert was added').not.toMatch(/setTitle\(title\)|setTitle\(prev/)
  })

  it('a failed rewind reports as an error, not as something the assistant said', () => {
    const chat = strip(F('features/ChatPage.tsx'))
    const fn = chat.slice(chat.indexOf('async function rewindToTurn('), chat.indexOf('async function transcribe('))
    expect(fn, 'the failure names its subject through the shared reporter')
      .toMatch(/reportActionFailure\(`rewind to turn/)
    expect(fn, 'the transcript no longer carries the failure').not.toContain('Rewind failed')
    expect(fn, 'no catch appends an assistant turn').not.toMatch(/catch[\s\S]{0,200}assistantTurn\(/)
    expect(fn.split('assistantTurn(').length - 1, 'both success notices remain').toBe(2)
  })
})
