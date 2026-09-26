import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ApprovalCard, REMEMBER_SCOPES } from './ApprovalCard'
import type { ApprovalSegment } from './chatTypes'
import { resetDataStore, writeQuery } from '../../shared/data/data'
import type { AutonomyLadder } from '../../shared/data/api'


const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'a1', tool: 'bash', toolKind: 'write', ...over,
})

const scopeTab = (label: string) => screen.getByRole('tab', { name: label })
const grant = () => screen.getByRole('group', { name: /permission needed to run/i })
const reach = () => grant().textContent?.split('this grants')[1]?.split('Remember this choice')[0] ?? ''

describe('ApprovalCard — the four zones', () => {
  it('renders what, why, what-it-can-touch and how-far-it-reaches', () => {
    const { container } = render(
      <ApprovalCard
        seg={seg({ tool: 'bash', input: 'rm -rf /tmp/scratch', purpose: 'Clearing the scratch dir before the rebuild', risk: 'destructive' })}
        onAct={() => {}}
      />,
    )
    expect(grant().getAttribute('data-slot')).toBe('permission-grant')
    expect(screen.getByRole('group', { name: 'Tool arguments' }).textContent).toBe('rm -rf /tmp/scratch')
    expect(container.textContent).toContain('Clearing the scratch dir before the rebuild')
    expect(reach()).toContain('Runs a command')
    expect(reach()).not.toContain('Reads only')
    expect(screen.getByRole('tablist', { name: 'Remember this choice' })).toBeTruthy()
    expect(container.textContent).toContain('Nothing is remembered. The next tool call asks again.')
  })

  it('omits the WHY zone rather than inventing one when the runner gave no purpose', () => {
    const { container } = render(<ApprovalCard seg={seg({ purpose: undefined })} onAct={() => {}} />)
    expect(container.textContent).not.toMatch(/purpose/i)
  })

  it('shows the risk chip when the wire carried a risk, and no chip when it did not', () => {
    for (const risk of ['safe', 'caution', 'destructive'] as const) {
      const { container, unmount } = render(<ApprovalCard seg={seg({ risk })} onAct={() => {}} />)
      expect(container.textContent, risk).toMatch(/Safe|Caution|Destructive/)
      unmount()
    }
    const { container } = render(<ApprovalCard seg={seg({ risk: undefined })} onAct={() => {}} />)
    expect(container.textContent).not.toMatch(/Safe|Caution|Destructive/)
  })

  it('survives a risk level this build has never heard of, claiming nothing extra', () => {
    const { container } = render(
      <ApprovalCard seg={seg({ tool: 'bash', risk: 'apocalyptic' as ApprovalSegment['risk'] })} onAct={() => {}} />,
    )
    expect(container.textContent).toContain('Permission needed')
    expect(container.textContent).not.toContain('Reads only')
  })
})

describe('ApprovalCard — the blast-radius zone never over-claims', () => {
  it('renders NO facet zone at all when the inputs establish nothing', () => {
    const { container } = render(<ApprovalCard seg={seg({ tool: 'ponder' })} onAct={() => {}} />)
    expect(container.textContent).toContain('Permission needed')
    expect(container.textContent).not.toContain('this grants')
    for (const negative of ['No writes', 'No network', 'No shell', 'Not read', 'no writes', 'none']) {
      expect(container.textContent, negative).not.toContain(negative)
    }
  })

  it('shows only the ESTABLISHED facets, never the full four with on/off states', () => {
    render(<ApprovalCard seg={seg({ tool: 'web_fetch', risk: 'caution' })} onAct={() => {}} />)
    expect(reach()).toContain('Uses the network')
    expect(reach()).not.toContain('Runs a command')
    expect(reach()).not.toContain('Reads only')
  })

  it('claims a read only on positive evidence, and both facets for a read-only shell call', () => {
    render(<ApprovalCard seg={seg({ tool: 'bash', risk: 'safe' })} onAct={() => {}} />)
    expect(reach()).toContain('Runs a command')
    expect(reach()).toContain('Reads only')
    expect(reach()).not.toContain('Uses the network')
  })
})

describe('ApprovalCard — remember-scope is a closed set that maps to real backend actions', () => {
  const BACKEND_ACTIONS = ['approved', 'rejected', 'trust', 'trust_agent', 'trust_reads', 'yolo']

  it('every scope posts a distinct action the backend already implements', () => {
    const actions = REMEMBER_SCOPES.map((s) => s.action)
    expect(new Set(actions).size).toBe(actions.length)
    for (const a of actions) expect(BACKEND_ACTIONS, a).toContain(a)
  })

  it('every scope is reachable and posts exactly its own action — no unmapped option', () => {
    for (const s of REMEMBER_SCOPES) {
      const onAct = vi.fn()
      const { unmount } = render(<ApprovalCard seg={seg()} onAct={onAct} />)
      fireEvent.click(scopeTab(s.label))
      fireEvent.click(screen.getByRole('button', { name: new RegExp(`^Allow bash`) }))
      expect(onAct, s.key).toHaveBeenCalledWith('a1', s.action)
      unmount()
    }
  })

  it('starts on the narrowest scope, so an unmodified Allow remembers nothing', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg()} onAct={onAct} />)
    expect(scopeTab('Just this once').getAttribute('aria-selected')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: /^Allow bash/ }))
    expect(onAct).toHaveBeenCalledWith('a1', 'approved')
  })

  it('states the promise for the selected scope in visible text, and updates it on change', () => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    fireEvent.click(scopeTab('This chat'))
    expect(container.textContent).toContain('Every tool in this chat runs without asking')
    expect(container.textContent).not.toMatch(/always for this tool|only this tool|this tool from now on/i)
    fireEvent.click(scopeTab('This agent'))
    expect(container.textContent).toContain('Saved on this agent')
  })

  it('carries the scope into the Allow control\'s accessible name', () => {
    render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    expect(screen.getByRole('button', { name: /^Allow bash — just this once: Nothing is remembered/ })).toBeTruthy()
    fireEvent.click(scopeTab('This agent'))
    expect(screen.getByRole('button', { name: /^Allow bash — this agent: Saved on this agent/ })).toBeTruthy()
  })

  it('denies single-shot whatever the scope says, and says so in the name', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg()} onAct={onAct} />)
    fireEvent.click(scopeTab('This agent'))
    const deny = screen.getByRole('button', { name: 'Deny bash — nothing is remembered' })
    fireEvent.click(deny)
    expect(onAct).toHaveBeenCalledWith('a1', 'rejected')
  })
})

describe('ApprovalCard — the brief describes, it never advocates', () => {
  const ADVOCACY = [
    /looks safe/i, /safe to (run|allow|approve)/i, /recommend/i, /we suggest/i, /suggested/i,
    /should (allow|approve|be fine)/i, /probably/i, /harmless/i, /no risk/i, /low risk/i,
    /go ahead/i, /nothing to worry/i, /usually fine/i, /it'?s fine/i, /trusted tool/i,
  ]

  function readableText(container: HTMLElement): string {
    const attrs = [...container.querySelectorAll('[title],[aria-label]')]
      .flatMap((el) => [el.getAttribute('title'), el.getAttribute('aria-label')])
      .filter(Boolean)
    return [container.textContent ?? '', ...attrs].join(' \n ')
  }

  it('contains no advocacy copy in any zone, at any risk level, under any scope', () => {
    for (const risk of ['safe', 'caution', 'destructive'] as const) {
      const { container, unmount } = render(
        <ApprovalCard seg={seg({ tool: 'bash', input: 'ls -la', purpose: 'Listing the repo root', risk })} onAct={() => {}} />,
      )
      for (const s of REMEMBER_SCOPES) {
        fireEvent.click(scopeTab(s.label))
        const text = readableText(container)
        expect(text).toContain('Permission needed')
        expect(text.length, `${risk}/${s.key}`).toBeGreaterThan(200)
        for (const pattern of ADVOCACY) expect(text, `${risk}/${s.key} ${pattern}`).not.toMatch(pattern)
      }
      unmount()
    }
  })

  it('does not make Allow the visual primary', () => {
    render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    const allow = screen.getByRole('button', { name: /^Allow bash/ })
    const deny = screen.getByRole('button', { name: /^Deny bash/ })
    expect(allow.className).not.toContain('inkButton')
    expect(deny.className).toContain('text-red-600')
  })

  it('focuses and pre-submits nothing on arrival', () => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    expect(container.querySelectorAll('[autofocus]')).toHaveLength(0)
    const buttons = [...container.querySelectorAll('button')]
    expect(buttons.length).toBeGreaterThan(0)
    for (const b of buttons) expect(b.getAttribute('type')).toBe('button')
    expect(document.activeElement).toBe(document.body)
  })
})

describe('ApprovalCard — connected PermissionGrant decisions', () => {
  it('shows a governing rung from the real autonomy query cache when the provider owns this tool', () => {
    const ladder: AutonomyLadder = {
      rungs: ['one_tap'],
      rung_meta: [{ key: 'one_tap', label: 'One tap', hint: 'Ask before running' }],
      incident_active: false,
      reversals: [],
      types: [{
        key: 'action.shell', floor: 'one_tap', ceiling: 'one_tap', leaves_machine: false,
        providers: ['bash'], resolved_rung: 'one_tap', granted_rung: 'one_tap',
        held_by_incident: false, authority: 'Owner choice', granted_at: '', evidence_window: '',
        demotions: [], eligible: false, next_rung: '', record: '', clean_approvals: 0,
        rejections: 0, observed_days: 0, cooldown_until: '',
      }],
    }
    writeQuery('autonomy:ladder', ladder, true)
    try {
      render(<ApprovalCard seg={seg()} onAct={() => {}} />)
      expect(screen.getByText('One tap')).toBeInTheDocument()
      expect(grant().textContent).toContain('Permission needed')
    } finally {
      resetDataStore()
    }
  })

  it('uses one donor permission card and one set of real approval actions', () => {
    render(<ApprovalCard seg={seg({ input: 'pwd', purpose: 'Check the current directory' })} onAct={() => {}} />)
    expect(document.querySelectorAll('[data-slot="permission-grant"]')).toHaveLength(1)
    expect(grant().textContent).toContain('Check the current directory')
    expect(screen.getByRole('group', { name: 'Tool arguments' }).textContent).toBe('pwd')
    expect(screen.getAllByRole('button', { name: /^Allow bash/ })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /^Deny bash/ })).toHaveLength(1)
  })

  it('never claims a requester when the wire has no requester identity', () => {
    render(<ApprovalCard seg={seg({ tool: 'ponder', risk: undefined })} onAct={() => {}} />)
    expect(grant().textContent).not.toContain('requested by')
    expect(grant().textContent).not.toContain('this grants')
  })

  it('keeps the selected backend scope even when a narrower tool type hides agent trust', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg({ toolKind: 'read' })} onAct={onAct} />)
    expect(screen.queryByRole('tab', { name: 'This agent' })).toBeNull()
    fireEvent.click(scopeTab('This chat'))
    fireEvent.click(screen.getByRole('button', { name: /^Allow bash/ }))
    expect(onAct).toHaveBeenCalledWith('a1', 'trust')
  })

  it('falls back to once when the live tool kind removes an already selected agent scope', () => {
    const onAct = vi.fn()
    const view = render(<ApprovalCard seg={seg({ toolKind: 'write' })} onAct={onAct} />)
    fireEvent.click(scopeTab('This agent'))
    view.rerender(<ApprovalCard seg={seg({ toolKind: 'read' })} onAct={onAct} />)
    expect(screen.queryByRole('tab', { name: 'This agent' })).toBeNull()
    expect(screen.getByRole('button', { name: /^Allow bash — just this once/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^Allow bash/ }))
    expect(onAct).toHaveBeenCalledWith('a1', 'approved')
  })

  it('prevents an empty revision and sends the exact trimmed revision once entered', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg({ canRevise: true })} onAct={onAct} />)
    const revise = screen.getByRole('button', { name: /^Request a revised bash action/ }) as HTMLButtonElement
    expect(revise.disabled).toBe(true)
    fireEvent.change(screen.getByRole('textbox', { name: 'How should this action change?' }), { target: { value: '  limit to /tmp  ' } })
    expect(revise.disabled).toBe(false)
    fireEvent.click(revise)
    expect(onAct).toHaveBeenCalledTimes(1)
    expect(onAct).toHaveBeenCalledWith('a1', 'revised', 'limit to /tmp')
  })

  it('shows the actual resolved backend outcome without pending decision controls', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg({ resolved: 'trust' })} onAct={onAct} />)
    expect(grant().textContent).toContain('trusted for this chat')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Allow bash/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Deny bash/ })).toBeNull()
    expect(onAct).not.toHaveBeenCalled()
  })
})
