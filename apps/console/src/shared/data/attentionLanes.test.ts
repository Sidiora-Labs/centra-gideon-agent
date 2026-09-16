import { describe, it, expect } from 'vitest'
import { LANES, laneFor, isKnownKind, KNOWN_KINDS, toLanes } from './attentionLanes'
import type { ActivityInput, ApprovalInput, AttentionInput, Lane, LaneCard } from './attentionLanes'
import type { InboxItemKind, InboxItemStatus } from './api'


function mkItem(over: Partial<AttentionInput> = {}): AttentionInput {
  return {
    id: 'i1',
    status: 'pending',
    message: 'a message',
    sender_name: 'Someone',
    channel_name: 'general',
    ...over,
  }
}

function mkApproval(over: Partial<ApprovalInput> = {}): ApprovalInput {
  return { id: 'req-1', source: 'chat', tool: 'Bash', session: 's1', ts: 1000, ...over }
}

function mkSession(over: Partial<ActivityInput> = {}): ActivityInput {
  return { key: 'sess-1', title: 'Refactor the ledger', running: true, stopping: false, pending_approval: false, ...over }
}

const ALL_KINDS: InboxItemKind[] = [
  'message', 'mention', 'email', 'agent_request', 'proposal', 'needs_input', 'digest', 'system',
  'user_note',
]
const ALL_STATUSES: InboxItemStatus[] = ['pending', 'seen', 'sent', 'dismissed', 'handled', 'filtered']

function allCards(lanes: Record<Lane, LaneCard[]>): LaneCard[] {
  return LANES.flatMap((lane) => lanes[lane])
}

describe('the closed kind vocabulary', () => {
  it('covers exactly the nine ItemKind members inbox.py declares', () => {
    expect([...KNOWN_KINDS].sort()).toEqual([...ALL_KINDS].sort())
    expect(KNOWN_KINDS).toHaveLength(9)
  })
})

describe('laneFor — one item, one lane', () => {
  it('puts one item of each derivable lane in that lane and in no other', () => {
    const cases: Array<[Lane, AttentionInput]> = [
      ['needs-approval', mkItem({ id: 'a', item_kind: 'agent_request', refs: { approval: 'req-9' } })],
      ['your-turn', mkItem({ id: 'b', item_kind: 'needs_input' })],
      ['idle', mkItem({ id: 'c', item_kind: 'digest' })],
    ]
    for (const [expected, item] of cases) {
      expect(laneFor(item)).toBe(expected)
    }

    const lanes = toLanes(cases.map(([, item]) => item), [])
    for (const [expected, item] of cases) {
      expect(lanes[expected].map((c) => c.id)).toContain(item.id)
      for (const other of LANES) {
        if (other === expected) continue
        expect(lanes[other].map((c) => c.id)).not.toContain(item.id)
      }
    }
  })

  it('routes both remaining attention kinds by what they ask for', () => {
    expect(laneFor(mkItem({ item_kind: 'proposal' }))).toBe('your-turn')
    expect(laneFor(mkItem({ item_kind: 'system' }))).toBe('idle')
  })

  it('treats a missing kind as the backend default (message), not as unknown', () => {
    expect(laneFor(mkItem({ item_kind: undefined }))).toBeNull()
  })
})

describe('precedence — an item qualifying twice appears exactly once', () => {
  it('gives needs-approval precedence over your-turn for a mirrored approval', () => {
    const plain = mkItem({ id: 'plain', item_kind: 'agent_request' })
    const mirror = mkItem({ id: 'mirror', item_kind: 'agent_request', refs: { approval: 'req-1' } })
    expect(laneFor(plain)).toBe('your-turn')
    expect(laneFor(mirror)).toBe('needs-approval')
  })

  it('collapses the approval and its inbox mirror into ONE card, and keeps the approval', () => {
    const approvals = [mkApproval({ id: 'req-1', tool: 'Write' })]
    const mirror = mkItem({ id: 'mirror', item_kind: 'agent_request', refs: { approval: 'req-1' } })
    const lanes = toLanes([mirror], approvals)

    expect(allCards(lanes)).toHaveLength(1)
    expect(lanes['needs-approval']).toHaveLength(1)
    expect(lanes['needs-approval'][0].origin).toBe('approval')
    expect(lanes['needs-approval'][0].title).toBe('Write')
  })

  it('keeps the mirror when its approval is NOT in the same snapshot', () => {
    const mirror = mkItem({ id: 'mirror', item_kind: 'agent_request', refs: { approval: 'gone' } })
    const lanes = toLanes([mirror], [mkApproval({ id: 'req-other' })])
    expect(allCards(lanes)).toHaveLength(2)
    expect(lanes['needs-approval'].map((c) => c.origin).sort()).toEqual(['approval', 'inbox'])
  })

  it('total cards across the four lanes equals the inputs minus the nulls, with no key repeated', () => {
    const items = [
      mkItem({ id: 'k1', item_kind: 'needs_input' }),
      mkItem({ id: 'k2', item_kind: 'proposal' }),
      mkItem({ id: 'k3', item_kind: 'digest' }),
      mkItem({ id: 'k4', item_kind: 'message' }),
      mkItem({ id: 'k5', item_kind: 'needs_input', status: 'handled' }),
      mkItem({ id: 'k6', item_kind: 'nonsense' as unknown as InboxItemKind }),
      mkItem({ id: 'k7', item_kind: 'agent_request' }),
    ]
    const approvals = [mkApproval({ id: 'req-1' }), mkApproval({ id: 'req-2' })]
    const activity = [mkSession({ key: 'sess-1' })]

    const placed = items.filter((i) => laneFor(i) !== null)
    expect(placed).toHaveLength(4)

    const lanes = toLanes(items, approvals, activity)
    const cards = allCards(lanes)
    expect(cards).toHaveLength(placed.length + approvals.length + activity.length)
    expect(new Set(cards.map((c) => c.key)).size).toBe(cards.length)
    for (const lane of LANES) for (const c of lanes[lane]) expect(c.lane).toBe(lane)
  })
})

describe('an unknown kind is a refusal, not an idle row', () => {
  it('returns null for a kind this build does not know — and a known kind in the same breath lands', () => {
    const unknown = mkItem({ id: 'u', item_kind: 'quantum_nudge' as unknown as InboxItemKind })
    const known = mkItem({ id: 'k', item_kind: 'needs_input' })

    expect(laneFor(known)).toBe('your-turn')
    expect(laneFor(unknown)).toBeNull()
    expect(isKnownKind('quantum_nudge')).toBe(false)
    expect(isKnownKind('needs_input')).toBe(true)

    const lanes = toLanes([unknown, known], [])
    for (const lane of LANES) expect(lanes[lane].map((c) => c.id)).not.toContain('u')
    expect(lanes['idle']).toHaveLength(0)
    expect(lanes['your-turn'].map((c) => c.id)).toEqual(['k'])
  })
})

describe('channel-shaped kinds are excluded from this surface', () => {
  it('drops message, mention and email while the attention kinds land', () => {
    for (const kind of ['message', 'mention', 'email'] as InboxItemKind[]) {
      expect(laneFor(mkItem({ item_kind: kind }))).toBeNull()
    }
    const lanes = toLanes(
      [
        mkItem({ id: 'm', item_kind: 'message' }),
        mkItem({ id: 'n', item_kind: 'mention' }),
        mkItem({ id: 'e', item_kind: 'email' }),
        mkItem({ id: 'p', item_kind: 'proposal' }),
      ],
      [],
    )
    expect(allCards(lanes).map((c) => c.id)).toEqual(['p'])
  })
})

describe('status decides whether an item is still asking', () => {
  it('keeps pending and seen, drops the closed statuses', () => {
    expect(laneFor(mkItem({ item_kind: 'needs_input', status: 'pending' }))).toBe('your-turn')
    expect(laneFor(mkItem({ item_kind: 'needs_input', status: 'seen' }))).toBe('your-turn')
    for (const status of ['handled', 'dismissed', 'filtered', 'sent'] as InboxItemStatus[]) {
      expect(laneFor(mkItem({ item_kind: 'needs_input', status }))).toBeNull()
    }
  })

  it('fails OPEN on a missing or unrecognised status', () => {
    expect(laneFor(mkItem({ item_kind: 'needs_input', status: undefined as unknown as InboxItemStatus }))).toBe('your-turn')
    expect(laneFor(mkItem({ item_kind: 'needs_input', status: 'snoozed' as unknown as InboxItemStatus }))).toBe('your-turn')
  })
})

describe('working is the lane the attention store cannot prove', () => {
  it('is never produced by laneFor, for any kind × status pair', () => {
    let derivable = 0
    for (const kind of ALL_KINDS) {
      for (const status of ALL_STATUSES) {
        const lane = laneFor(mkItem({ item_kind: kind, status }))
        expect(lane).not.toBe('working')
        if (lane !== null) derivable += 1
      }
    }
    expect(derivable).toBeGreaterThan(0)
  })

  it('is populated only from observed session activity', () => {
    const lanes = toLanes([mkItem({ item_kind: 'needs_input' })], [], [
      mkSession({ key: 'run', running: true }),
      mkSession({ key: 'wind', running: false, stopping: true }),
      mkSession({ key: 'quiet', running: false, stopping: false }),
    ])
    expect(lanes['working'].map((c) => c.id).sort()).toEqual(['run', 'wind'])
    expect(lanes['working'].map((c) => c.subtitle).sort()).toEqual(['running', 'stopping'])
    expect(allCards(lanes).map((c) => c.id)).not.toContain('quiet')
  })

  it('is empty — not guessed — when no activity is supplied', () => {
    const lanes = toLanes([mkItem({ item_kind: 'needs_input', created_at: 1 })], [mkApproval()])
    expect(lanes['working']).toEqual([])
  })

  it('does not mirror a session pending_approval into needs-approval', () => {
    const lanes = toLanes([], [], [mkSession({ key: 'blocked', running: true, pending_approval: true })])
    expect(lanes['needs-approval']).toEqual([])
    expect(lanes['working'].map((c) => c.id)).toEqual(['blocked'])
  })
})

describe('ordering inside a lane', () => {
  it('ranks the lanes that ask you something OLDEST first', () => {
    const lanes = toLanes(
      [
        mkItem({ id: 'mid', item_kind: 'needs_input', created_at: 200 }),
        mkItem({ id: 'newest', item_kind: 'needs_input', created_at: 300 }),
        mkItem({ id: 'oldest', item_kind: 'needs_input', created_at: 100 }),
      ],
      [
        mkApproval({ id: 'a-mid', ts: 20 }),
        mkApproval({ id: 'a-new', ts: 30 }),
        mkApproval({ id: 'a-old', ts: 10 }),
      ],
    )
    expect(lanes['your-turn'].map((c) => c.id)).toEqual(['oldest', 'mid', 'newest'])
    expect(lanes['needs-approval'].map((c) => c.id)).toEqual(['a-old', 'a-mid', 'a-new'])
  })

  it('ranks idle NEWEST first — nothing there is overdue', () => {
    const lanes = toLanes(
      [
        mkItem({ id: 'mid', item_kind: 'digest', created_at: 200 }),
        mkItem({ id: 'newest', item_kind: 'system', created_at: 300 }),
        mkItem({ id: 'oldest', item_kind: 'digest', created_at: 100 }),
      ],
      [],
    )
    expect(lanes['idle'].map((c) => c.id)).toEqual(['newest', 'mid', 'oldest'])
  })

  it('sorts an undated row LAST in either direction, and breaks ties deterministically', () => {
    const undatedInAsc = toLanes(
      [
        mkItem({ id: 'dated', item_kind: 'needs_input', created_at: 500 }),
        mkItem({ id: 'undated', item_kind: 'needs_input' }),
      ],
      [],
    )
    expect(undatedInAsc['your-turn'].map((c) => c.id)).toEqual(['dated', 'undated'])

    const undatedInDesc = toLanes(
      [
        mkItem({ id: 'dated', item_kind: 'digest', created_at: 500 }),
        mkItem({ id: 'undated', item_kind: 'digest' }),
      ],
      [],
    )
    expect(undatedInDesc['idle'].map((c) => c.id)).toEqual(['dated', 'undated'])

    const tied = toLanes(
      [
        mkItem({ id: 'b', item_kind: 'needs_input', created_at: 7 }),
        mkItem({ id: 'a', item_kind: 'needs_input', created_at: 7 }),
      ],
      [],
    )
    expect(tied['your-turn'].map((c) => c.id)).toEqual(['a', 'b'])
  })

  it('falls back to the stringified `ts` tail when created_at is absent', () => {
    const lanes = toLanes(
      [
        mkItem({ id: 'later', item_kind: 'needs_input', ts: '900.5' }),
        mkItem({ id: 'earlier', item_kind: 'needs_input', ts: '100.25' }),
      ],
      [],
    )
    expect(lanes['your-turn'].map((c) => c.id)).toEqual(['earlier', 'later'])
    expect(lanes['your-turn'][0].at).toBe(100.25)
  })
})

describe('the shape the component indexes', () => {
  it('returns all four lanes, empty, for empty input', () => {
    const lanes = toLanes([], [])
    expect(Object.keys(lanes)).toEqual([...LANES])
    for (const lane of LANES) expect(lanes[lane]).toEqual([])
  })

  it('returns all four lanes even when only one is populated', () => {
    const lanes = toLanes([], [mkApproval()])
    expect(Object.keys(lanes)).toEqual([...LANES])
    expect(lanes['your-turn']).toEqual([])
    expect(lanes['working']).toEqual([])
    expect(lanes['idle']).toEqual([])
  })
})

describe('malformed input does not blank the surface', () => {
  it('survives nulls, non-objects, missing fields and non-array arguments', () => {
    const junk = [
      null,
      undefined,
      'a string',
      42,
      {},
      { id: 'no-status', item_kind: 'needs_input' },
      { id: '', item_kind: 'needs_input', status: 'pending' },
      { id: 'bad-refs', item_kind: 'agent_request', status: 'pending', refs: 'not-an-object' },
      { id: 'null-refs', item_kind: 'agent_request', status: 'pending', refs: null },
      { id: 'nan-time', item_kind: 'needs_input', status: 'pending', created_at: Number.NaN },
      { id: 'junk-ts', item_kind: 'needs_input', status: 'pending', ts: 'not-a-number' },
    ] as unknown as AttentionInput[]
    const badApprovals = [null, {}, { id: 'ok' }, 'x'] as unknown as ApprovalInput[]
    const badActivity = [null, {}, { key: 'k', running: true }, 7] as unknown as ActivityInput[]

    let lanes!: ReturnType<typeof toLanes>
    expect(() => { lanes = toLanes(junk, badApprovals, badActivity) }).not.toThrow()
    expect(Object.keys(lanes)).toEqual([...LANES])

    const ids = allCards(lanes).map((c) => c.id)
    expect(ids).toContain('no-status')
    expect(ids).toContain('nan-time')
    expect(ids).not.toContain('')
    expect(lanes['needs-approval'].map((c) => c.id)).toContain('ok')
    expect(lanes['working'].map((c) => c.id)).toContain('k')
    expect(lanes['your-turn'].map((c) => c.id)).toContain('bad-refs')

    expect(() => toLanes(null as unknown as AttentionInput[], undefined as unknown as ApprovalInput[])).not.toThrow()
    expect(Object.keys(toLanes(null as unknown as AttentionInput[], 'nope' as unknown as ApprovalInput[]))).toEqual([...LANES])
    expect(laneFor(null as unknown as AttentionInput)).toBeNull()
    expect(laneFor(undefined as unknown as AttentionInput)).toBeNull()
  })

  it('titles a card from whatever the row actually carried', () => {
    const lanes = toLanes(
      [
        mkItem({ id: 'multi', item_kind: 'needs_input', message: 'first line\nsecond line' }),
        mkItem({ id: 'summary', item_kind: 'needs_input', message: '   ', context_summary: 'from the summary' }),
        mkItem({ id: 'blank', item_kind: 'needs_input', message: '', context_summary: '' }),
      ],
      [],
    )
    const byId = new Map(lanes['your-turn'].map((c) => [c.id, c]))
    expect(byId.get('multi')!.title).toBe('first line')
    expect(byId.get('summary')!.title).toBe('from the summary')
    expect(byId.get('blank')!.title).toBe('(no message)')
  })
})
