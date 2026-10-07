import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { jsxTags } from '../testing/jsxContracts'
import { resolveSendButton, sendButtonIsActive } from './composer/sendButtonState'
import { Button } from './Button'


describe('an unavailable button says why', () => {
  it('stays focusable and reachable', () => {
    render(<Button disabled disabledReason="Enter a name first">Create project</Button>)
    const b = screen.getByRole('button', { name: 'Create project' })
    expect(b.hasAttribute('disabled'), 'the native attribute would remove the tab stop').toBe(false)
    expect(b.getAttribute('aria-disabled')).toBe('true')
  })

  it('keeps its accessible NAME clean', () => {
    render(<Button disabled disabledReason="Enter a name first">Create project</Button>)
    expect(screen.getByRole('button').textContent).toBe('Create project')
    expect(screen.getByRole('button', { name: 'Create project' })).toBeTruthy()
  })

  it('carries the reason where AT and sighted users both get it', () => {
    render(<Button disabled disabledReason="Enter a project name first">Create project</Button>)
    expect(screen.getByRole('button').getAttribute('title')).toBe('Enter a project name first')
  })

  it('appends to an existing title rather than replacing it', () => {
    render(<Button disabled disabledReason="Enter a name" title="Save (⌘S)">Save</Button>)
    expect(screen.getByRole('button').getAttribute('title')).toBe('Save (⌘S) — Enter a name')
  })

  it('refuses the click', () => {
    const onClick = vi.fn()
    render(<Button disabled disabledReason="nope" onClick={onClick}>Go</Button>)
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('does nothing when the button is ENABLED', () => {
    const onClick = vi.fn()
    render(<Button disabledReason="stale reason" onClick={onClick}>Go</Button>)
    const b = screen.getByRole('button')
    expect(b.getAttribute('aria-disabled')).toBeNull()
    expect(b.getAttribute('title')).toBeNull()
    fireEvent.click(b)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('stays NATIVELY disabled with no reason given', () => {
    render(<Button disabled>Go</Button>)
    expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true)
  })

  it('stays NATIVELY disabled while loading, even with a reason', () => {
    render(<Button loading disabledReason="x">Go</Button>)
    expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true)
  })
})


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('the migrated submits pass a reason', () => {
  const ADOPTERS = [
    'features/projects/ProjectsSection.tsx',
    'features/tasks/TaskForm.tsx',
    'features/tasks/TaskCreatePage.tsx',
    'features/agents/AgentCreatePage.tsx',
    'features/prompts/PromptCreatePage.tsx',
    'features/settings/MemoryPanel.tsx',
    'features/settings/MultiInstanceCard.tsx',
    'features/settings/DesignPanel.tsx',
    'features/prompts/PromptDetail.tsx',
    'features/prompts/SnippetDetail.tsx',
    'features/tasks/TaskDetail.tsx',
    'features/inbox/InboxDetail.tsx',
    'features/triggers/LifecycleDetail.tsx',
    'features/schedule/ScheduleDetail.tsx',
    'features/code/WorkspacePicker.tsx',
    'features/workflows/WorkflowAsk.tsx',
    'features/workflows/SteeringPanel.tsx',
    'features/files/comments/CommentLayer.tsx',
    'features/loops/LoopCockpitPage.tsx',
    'features/apps/AppsSection.tsx',
    'features/ChatPage.tsx',
  ]

  for (const rel of ADOPTERS) {
    it(`${rel} explains its disabled submit`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must pass disabledReason').toMatch(/disabledReason=\{/)
      expect(src, 'the reason must be conditional, not a constant').toMatch(/disabledReason=\{[^}]*\?/)
      expect(src, 'the reason must fall back to undefined or the shared busy reason when nothing is missing')
        .toMatch(/disabledReason=\{[^}]*(undefined|BUSY_REASON)/)
    })
  }

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})


function gatedSubmits(): Array<{ file: string; line: number; tag: string }> {
  const out: Array<{ file: string; line: number; tag: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const site of jsxTags(text, ['Button', 'button', 'motion.button'])) {
      const gate = site.attributes.get('disabled') ?? site.attributes.get('aria-disabled') ?? ''
      if (/!\w+[\w.]*\.trim\(\)|length === 0|!can[A-Z]\w*/.test(gate) || /unavailableWhen\(/.test(site.tag)) {
        out.push({ file: abs.slice(SRC.length + 1), line: site.line, tag: site.tag })
      }
    }
  }
  return out
}

describe('the unexplained-submit tail only shrinks', () => {
  const gated = gatedSubmits()
  const unexplained = gated.filter((t) => {
    const site = jsxTags(t.tag)[0]
    return !site.attributes.has('disabledReason') && !/unavailableWhen\(/.test(t.tag) &&
      !(site.attributes.has('aria-description') && site.attributes.has('title'))
  })

  it('finds the gated submits (not vacuously green)', () => {
    expect(gated.length, 'the matcher must find validity-gated submits').toBeGreaterThan(30)
  })

  it('has NO unexplained validity-gated submit left', () => {
    expect(
      unexplained.length,
      `${unexplained.length} validity-gated submits still cannot say why they are unavailable. ` +
        'Pass `disabledReason` on a <Button>, or spread `unavailableWhen()` on a raw <button>:\n  ' +
        unexplained.map((t) => `${t.file}:${t.line}`).join('\n  '),
    ).toBe(0)
  })

  it('every holdout is a RAW button, not the primitive', () => {
    const primitives = unexplained.filter((t) => /^<Button\b/.test(t.tag)).map((t) => `${t.file}:${t.line}`)
    expect(
      primitives,
      'a Button-primitive submit can pass disabledReason directly — do not leave it silent:\n  ' +
        primitives.join('\n  '),
    ).toEqual([])
  })
})


describe('a reason never rides the accessible name', () => {
  const composer = readFileSync(join(SRC, 'shared/ui/Composer.tsx'), 'utf8')

  it('keeps the composer labels constant across states', () => {
    const labels = [...composer.matchAll(/(?:aria-)?label=(?:"([^"]*)"|\{([^}]*)\})/g)]
      .map((m) => (m[1] ?? m[2] ?? '').toLowerCase())
    for (const leaked of ['type something first', 'type a bit more first']) {
      const where = labels.filter((l) => l.includes(leaked))
      expect(
        where,
        `"${leaked}" is a reason — it belongs on disabledReason, not in the label/aria-label`,
      ).toEqual([])
    }
  })

  it('explains both gated composer controls', () => {
    expect(composer).toMatch(/disabledReason="Type something first"/)
    const send = jsxTags(composer, ['AssistantComposerSend'])[0]
    expect(send.attributes.get('aria-description')).toBe('{sendReason}')
    expect(send.attributes.get('title')).toBe('{sendReason}')
    expect(send.attributes.get('aria-disabled')).toBe("{action === 'send-disabled' || undefined}")
    expect(send.attributes.get('onClick')).toBe('{primaryClick}')
    expect(composer).toMatch(/surface\.action\.canSubmit \? surface\.submit : undefined/)
    const kind = resolveSendButton({ processing: false, streaming: false, canSend: false, canQueue: false, justSent: false })
    expect(kind).toBe('send-disabled')
    expect(sendButtonIsActive(kind)).toBe(false)
  })

  it('has no em-dash reason left in any aria-label in the tree', () => {
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      const text = readFileSync(abs, 'utf8')
      for (const m of text.matchAll(/(?:aria-)?label=["'{`][^"'`]*—\s*(type|enter|choose|pick|fill|add|name|answer|complete|write|set)\b[^"'`]*/gi)) {
        offenders.push(`${abs.slice(SRC.length + 1)}: ${m[0].slice(0, 78)}`)
      }
    }
    expect(offenders.length, `a reason is riding an accessible name:\n  ${offenders.join('\n  ')}`).toBe(0)
  })
})
