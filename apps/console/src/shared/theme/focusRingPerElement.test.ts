import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const ELEMENT_RING = /focus:ring-2\s+focus:ring-inset\s+focus:ring-primary\b/
const CONTAINER_RING =
  /focus-within:ring-2\s+focus-within:ring-inset\s+focus-within:ring-primary\b/

const INVENTORY: { file: string; anchor: string; ring: RegExp; what: string }[] = [
  {
    file: 'features/ChatPage.tsx',
    anchor: 'h-8 min-w-[200px] max-w-[420px] rounded-md bg-surface-high',
    ring: ELEMENT_RING,
    what: 'rename-this-chat input',
  },
  {
    file: 'features/ChatPage.tsx',
    anchor: 'w-full rounded-md bg-surface-high px-2 py-1.5',
    ring: ELEMENT_RING,
    what: 'auto-continue message textarea',
  },
  {
    file: 'features/ChatPage.tsx',
    anchor: 'w-16 rounded bg-surface-high',
    ring: ELEMENT_RING,
    what: 'auto-continue idle-seconds number input',
  },
  {
    file: 'features/ChatPage.tsx',
    anchor: 'w-14 rounded bg-surface-high',
    ring: ELEMENT_RING,
    what: 'auto-continue max-cycles number input',
  },
  {
    file: 'features/code/CodePlanReview.tsx',
    anchor: 'min-w-0 flex-1 bg-transparent text-on-surface-var',
    ring: ELEMENT_RING,
    what: 'task title input',
  },
  {
    file: 'features/code/CodePlanReview.tsx',
    anchor: 'min-w-0 flex-1 bg-transparent text-on-surface-low outline-none',
    ring: ELEMENT_RING,
    what: 'task description input',
  },
  {
    file: 'features/code/CodePlanReview.tsx',
    anchor: 'min-w-0 flex-1 bg-transparent text-on-surface outline-none',
    ring: ELEMENT_RING,
    what: 'add-a-task input',
  },
  {
    file: 'shared/ui/Combobox.tsx',
    anchor: 'w-full h-8 rounded-md bg-surface pl-8 pr-2',
    ring: ELEMENT_RING,
    what: 'combobox search input',
  },
  {
    file: 'features/chat/ChatActivityPanel.tsx',
    anchor: 'max-h-24 min-h-0 flex-1 resize-none bg-transparent',
    ring: CONTAINER_RING,
    what: 'ask-the-side composer',
  },
  {
    file: 'features/code/CodeCockpitPage.tsx',
    anchor: 'max-h-24 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent',
    ring: CONTAINER_RING,
    what: 'steer-the-worker composers (two call sites, identical chrome — both checked)',
  },
  {
    file: 'features/knowledge/KnowledgeCreatePage.tsx',
    anchor: 'flex-1 bg-transparent text-on-surface outline-none placeholder:text-on-surface-low',
    ring: CONTAINER_RING,
    what: 'bookmark URL field',
  },
  {
    file: 'features/knowledge/KnowledgeCreatePage.tsx',
    anchor: 'h-full w-full resize-none bg-transparent px-m py-2',
    ring: CONTAINER_RING,
    what: 'markdown body textarea',
  },
  {
    file: 'features/knowledge/KnowledgeDetail.tsx',
    anchor: 'flex-1 bg-transparent text-on-surface outline-none placeholder:text-on-surface-low',
    ring: CONTAINER_RING,
    what: 'bookmark URL field (edit)',
  },
  {
    file: 'features/knowledge/KnowledgeDetail.tsx',
    anchor: 'h-full w-full resize-none bg-transparent px-m py-2',
    ring: CONTAINER_RING,
    what: 'markdown body textarea (edit)',
  },
  {
    file: 'features/loop/LoopComposer.tsx',
    anchor: 'min-w-0 flex-1 bg-transparent text-on-surface outline-none',
    ring: CONTAINER_RING,
    what: 'codebase-path + reference-URL fields (identical chrome — both checked)',
  },
  {
    file: 'features/ChatPage.tsx',
    anchor: 'w-full resize-none bg-transparent px-s py-xs text-on-surface',
    ring: ELEMENT_RING,
    what: 'session-peek quick-reply composer',
  },
  {
    file: 'features/loops/LoopCockpitPage.tsx',
    anchor: 'w-full bg-transparent outline-none focus:ring-2',
    ring: ELEMENT_RING,
    what: 'nudge / answer-the-agent composer (autoFocus hid only the arrival, not the return)',
  },
]

const RINGED_BY_AN_ANCESTOR = [
  'shared/ui/RowHitTarget.tsx',
  'shared/ui/ListScaffold.tsx',
  'features/settings/bento.tsx',
  'app/shell/Onboarding.tsx',
  'shared/ui/forms.tsx',
]

function read(file: string): string {
  return readFileSync(join(SRC, file), 'utf8')
}

const CLASS_STRING = /className=(?:"([^"]*)"|\{`([^`]*)`\})/g

function classStrings(text: string): [string, number][] {
  const out: [string, number][] = []
  for (const m of text.matchAll(CLASS_STRING)) out.push([m[1] ?? m[2] ?? '', m.index])
  return out
}

function controlsMatching(text: string, anchor: string): string[] {
  return classStrings(text)
    .filter(([cls]) => cls.includes(anchor))
    .map(([cls]) => cls)
}

function containersOf(text: string, anchor: string): string[] {
  const all = classStrings(text)
  const out: string[] = []
  all.forEach(([cls], i) => {
    if (!cls.includes(anchor)) return
    for (let j = i - 1; j >= 0; j--) {
      if (/\brounded-/.test(all[j][0])) {
        out.push(all[j][0])
        return
      }
    }
    out.push('')
  })
  return out
}

describe('every control that kills its outline carries a replacement ring', () => {
  it.each(INVENTORY)('$file — $what', ({ file, anchor, ring }) => {
    const text = read(file)
    const matches =
      ring === CONTAINER_RING ? containersOf(text, anchor) : controlsMatching(text, anchor)
    expect(
      matches.length,
      `anchor no longer matches any class string in ${file}: "${anchor}"`,
    ).toBeGreaterThan(0)
    const unringed = matches.filter((cls) => !ring.test(cls))
    expect(
      unringed,
      `${unringed.length} of ${matches.length} matching control(s) in ${file} set outline-none, ` +
        `which defeats the global :focus-visible ring from design/tokens.css, and carry no ` +
        `replacement — keyboard focus lands on them invisibly. Restore the treatment, or move the ` +
        `ring to the container and record that here.`,
    ).toEqual([])
  })

  it('the ancestor-ringed controls still have the ancestor that rings them', () => {
    for (const file of RINGED_BY_AN_ANCESTOR) {
      const text = read(file)
      expect(text, `${file} relies on an ancestor ring that is now gone`).toMatch(
        /focus-within:ring-2|has-\[>button:focus-visible\]:ring-2/,
      )
    }
  })

  it('no two inventory entries duplicate a file+anchor pair', () => {
    const seen = new Set<string>()
    for (const { file, anchor } of INVENTORY) {
      const key = `${file}::${anchor}`
      expect(seen.has(key), `duplicate anchor in ${file}: "${anchor}"`).toBe(false)
      seen.add(key)
    }
  })
})
