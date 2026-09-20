import { describe, expect, it } from 'vitest'
import {
  eventDormancyReason, eventIsDormant, lifecycleEventMeta, storeToTrigger, scheduleToTrigger,
  EVENT_PATTERN_META, eventPatternMeta, eventSourceIcon, eventSourceLabel,
  appEventOptions, actionIsSendCapable, eventToTrigger, triggerStatusMeta,
} from './triggerMeta'
import type { TriggerVariables, Trigger as WireTrigger, ScheduleJob } from '../../shared/data/api'


const cat = (over: Partial<TriggerVariables> = {}): TriggerVariables => ({
  schedule: ['$NOW'],
  lifecycle: [
    { event: 'PreToolUse', label: 'Pre tool use', desc: 'Before a tool runs', vars: ['$EVENT'], blocking: true },
    { event: 'SessionEnd', label: 'Session end', desc: 'A session ends', vars: ['$EVENT'], blocking: false, dormant: true, dormant_reason: 'session teardown has no fire site' },
    { event: 'MemoryWrite', label: 'Memory write', desc: 'A memory is written', vars: ['$EVENT'], blocking: false, dormant: true },
  ],
  app_sources: [],
  ...over,
})

describe('eventIsDormant', () => {
  it('flags an event the server marked dormant', () => {
    expect(eventIsDormant(cat(), 'SessionEnd')).toBe(true)
  })

  it('does not flag a live event', () => {
    expect(eventIsDormant(cat(), 'PreToolUse')).toBe(false)
  })

  it('treats an unknown event as live, not dormant', () => {
    expect(eventIsDormant(cat(), 'SomeNewEvent')).toBe(false)
  })

  it('treats a still-loading catalog as live', () => {
    expect(eventIsDormant(null, 'SessionEnd')).toBe(false)
  })

  it('is false for an absent event name', () => {
    expect(eventIsDormant(cat(), undefined)).toBe(false)
    expect(eventIsDormant(cat(), '')).toBe(false)
  })
})

describe('eventDormancyReason', () => {
  it('returns the server-supplied reason', () => {
    expect(eventDormancyReason(cat(), 'SessionEnd')).toBe('session teardown has no fire site')
  })

  it('returns empty for a live event even if a reason were present', () => {
    const c = cat({
      lifecycle: [{ event: 'Stop', label: 'Stop', desc: '', vars: [], blocking: false, dormant: false, dormant_reason: 'stale text' }],
    })
    expect(eventDormancyReason(c, 'Stop')).toBe('')
  })

  it('returns empty when a dormant event carries no reason, so callers can supply a fallback', () => {
    expect(eventDormancyReason(cat(), 'MemoryWrite')).toBe('')
    expect(eventIsDormant(cat(), 'MemoryWrite')).toBe(true)
  })

  it('returns empty for an unknown event and a loading catalog', () => {
    expect(eventDormancyReason(cat(), 'Nope')).toBe('')
    expect(eventDormancyReason(null, 'SessionEnd')).toBe('')
  })
})

describe('lifecycleEventMeta', () => {
  it('carries the dormancy fields through so a caller can badge from one lookup', () => {
    const em = lifecycleEventMeta(cat(), 'SessionEnd')
    expect(em.label).toBe('Session end')
    expect(em.dormant).toBe(true)
  })

  it('falls back to an empty shell without inventing dormancy', () => {
    const em = lifecycleEventMeta(null, 'Whatever')
    expect(em.event).toBe('Whatever')
    expect(em.dormant).toBeUndefined()
  })
})


const storeRow = (over: Partial<WireTrigger> = {}): WireTrigger => ({
  kind: 'store', id: 'store:file:summarize-notes', raw_id: 'file:summarize-notes',
  name: 'Summarize notes', enabled: true, action: { provider: 'run-prompt', config: {} },
  store_kind: 'file', spec: { paths: ['~/notes/**'] }, broken: [],
  ...over,
})

describe('scheduleToTrigger', () => {
  const schedRow = (over: Partial<ScheduleJob> = {}): ScheduleJob => ({
    id: 'clock:nightly', name: 'Nightly digest', message: '', enabled: true,
    schedule: 'every day at 09:00', action: { provider: 'run-prompt', config: {} },
    ...over,
  })

  it('carries a broken schedule’s parse errors rather than hiding them', () => {
    const t = scheduleToTrigger(schedRow({ broken: ['unparseable cron expr'] }))
    expect(t.broken).toEqual(['unparseable cron expr'])
  })

  it('defaults broken to an empty array for a healthy schedule', () => {
    expect(scheduleToTrigger(schedRow()).broken).toEqual([])
  })

  it('preserves clock state, health, and the redacted boundary reason', () => {
    const t = scheduleToTrigger(schedRow({
      state: 'autopaused', health: 'failing', last_error: 'safe failure summary',
    } as Partial<ScheduleJob>))
    expect([t.state, t.health, t.lastError]).toEqual(['autopaused', 'failing', 'safe failure summary'])
  })
})

describe('storeToTrigger', () => {
  it('projects a file automation onto the list view-model', () => {
    const t = storeToTrigger(storeRow())
    expect(t.kind).toBe('store')
    expect(t.storeKind).toBe('file')
    expect(t.whenLabel).toBe('On file change')
    expect(t.actionLabel).toBe('Run Prompt')
    expect(t.enabled).toBe(true)
  })

  it('keeps the store id as rawId so mutations re-namespace correctly', () => {
    const t = storeToTrigger(storeRow())
    expect(t.id).toBe('store:file:summarize-notes')
    expect(t.rawId).toBe('file:summarize-notes')
  })

  it('carries a broken row rather than hiding it', () => {
    const t = storeToTrigger(storeRow({ broken: ['unknown trigger kind'] }))
    expect(t.broken).toEqual(['unknown trigger kind'])
  })

  it('falls back to a neutral label for an unknown store_kind', () => {
    const t = storeToTrigger(storeRow({ store_kind: 'brand_new_kind' }))
    expect(t.whenLabel).toBe('brand_new_kind')
  })

  it('labels each declared store kind', () => {
    const kinds: Array<[string, string]> = [
      ['file', 'On file change'],
      ['web_watch', 'On web page change'],
      ['idle', 'When idle'],
      ['run_completed', 'When a run finishes'],
      ['webhook', 'On webhook'],
    ]
    for (const [k, label] of kinds) {
      expect(storeToTrigger(storeRow({ store_kind: k })).whenLabel).toBe(label)
    }
  })
})


describe('EVENT_PATTERN_META', () => {
  it('has exactly one row per backend EVENT_PATTERNS member', () => {
    const patterns = EVENT_PATTERN_META.map((p) => p.pattern).sort()
    expect(patterns).toEqual(
      ['AppEvent', 'ContentMatch', 'InboxAddress', 'InboxMessage', 'InboxSender', 'MemoryKeyPattern', 'MemoryUpdate'],
    )
  })

  it('maps each pattern to the source the backend derives, never a free choice', () => {
    const bySource = (s: string) => EVENT_PATTERN_META.filter((p) => p.source === s).map((p) => p.pattern).sort()
    expect(bySource('inbox')).toEqual(['InboxAddress', 'InboxMessage', 'InboxSender'])
    expect(bySource('memory')).toEqual(['ContentMatch', 'MemoryKeyPattern', 'MemoryUpdate'])
    expect(bySource('app')).toEqual(['AppEvent'])
  })

  it('names a real spec matcher field (or null) for every pattern', () => {
    const allowed = new Set(['sender_glob', 'address_glob', 'key_glob', 'content_re', 'event_glob', null])
    for (const p of EVENT_PATTERN_META) expect(allowed.has(p.matcher)).toBe(true)
    expect(eventPatternMeta('AppEvent').matcher).toBe('event_glob')
    expect(eventPatternMeta('InboxMessage').matcher).toBeNull()
    expect(eventPatternMeta('MemoryUpdate').matcher).toBeNull()
    expect(eventPatternMeta('InboxSender').matcher).toBe('sender_glob')
    expect(eventPatternMeta('InboxAddress').matcher).toBe('address_glob')
    expect(eventPatternMeta('MemoryKeyPattern').matcher).toBe('key_glob')
    expect(eventPatternMeta('ContentMatch').matcher).toBe('content_re')
  })

  it('marks only InboxSender as matcher-required, mirroring the server gate', () => {
    const required = EVENT_PATTERN_META.filter((p) => p.matcherRequired).map((p) => p.pattern)
    expect(required).toEqual(['InboxSender'])
  })
})

describe('eventPatternMeta', () => {
  it('resolves a known pattern to its row', () => {
    expect(eventPatternMeta('InboxSender').label).toBe('Inbox message from a sender')
  })

  it('falls back to the first row for an unknown/absent pattern rather than crashing', () => {
    expect(eventPatternMeta('bogus').pattern).toBe('InboxMessage')
    expect(eventPatternMeta(undefined).pattern).toBe('InboxMessage')
  })
})

describe('eventSourceIcon', () => {
  it('gives a distinct icon for every source', () => {
    const icons = new Set(['inbox', 'memory', 'app'].map(eventSourceIcon))
    expect(icons.size).toBe(3)
  })

  it('falls back to the memory icon for an unknown source', () => {
    expect(eventSourceIcon('whatever')).toBe(eventSourceIcon('memory'))
  })
})

describe('eventSourceLabel', () => {
  it('names each source the way the badge and the copy both read it', () => {
    expect(eventSourceLabel('inbox')).toBe('Inbox')
    expect(eventSourceLabel('memory')).toBe('Memory')
    expect(eventSourceLabel('app')).toBe('App')
  })

  it('falls back to Memory for an unknown source rather than rendering blank', () => {
    expect(eventSourceLabel('whatever')).toBe('Memory')
  })
})


describe('appEventOptions', () => {
  const withSources = cat({
    app_sources: [
      { app: 'sample-source', label: 'Sample Source', events: [
        { event: 'thing_happened', source_event: 'app:sample-source:thing_happened' },
        { event: 'other_thing', source_event: 'app:sample-source:other_thing' },
      ] },
    ],
  })

  it('uses the backend source_event as the option VALUE, never a locally-built name', () => {
    const values = appEventOptions(withSources).map((o) => o.value)
    expect(values).toEqual(['app:sample-source:thing_happened', 'app:sample-source:other_thing'])
  })

  it('labels an option with the app first, so two apps sharing an event name stay distinguishable', () => {
    expect(appEventOptions(withSources)[0].label).toContain('Sample Source')
  })

  it('is empty when no app contributes a source, and when the catalog has not loaded', () => {
    expect(appEventOptions(cat())).toEqual([])
    expect(appEventOptions(null)).toEqual([])
  })
})


describe('actionIsSendCapable', () => {
  it('flags the bundled send-message provider', () => {
    expect(actionIsSendCapable('send-message')).toBe(true)
  })

  it('flags a future send-* channel provider', () => {
    expect(actionIsSendCapable('send-telegram')).toBe(true)
  })

  it('does not flag non-sending providers', () => {
    for (const p of ['bash', 'notify', 'run-prompt', 'create-task', 'webhook', 'run-workflow']) {
      expect(actionIsSendCapable(p)).toBe(false)
    }
  })

  it('does not flag a provider that merely contains "send" mid-name', () => {
    expect(actionIsSendCapable('resend-digest')).toBe(false)
  })

  it('is false for an absent provider', () => {
    expect(actionIsSendCapable(undefined)).toBe(false)
    expect(actionIsSendCapable('')).toBe(false)
  })
})

describe('eventToTrigger', () => {
  const wire = {
    kind: 'event', id: 'event:memo', raw_id: 'memo', name: 'On a memory write', enabled: true,
    pattern: 'MemoryKeyPattern', key_glob: 'project.acme.*', fire_count: 3,
    action: { provider: 'create-task', config: {} },
  } as unknown as WireTrigger

  it('supplies every presentation field the list row renders', () => {
    const t = eventToTrigger(wire)
    expect(t.whenIcon).toBeTruthy()
    expect(t.actionIcon).toBeTruthy()
    expect(t.whenTone).toMatch(/^var\(--color-/)
    expect(t.whenLabel).toBe(eventPatternMeta('MemoryKeyPattern').label)
    expect(t.actionLabel).toBeTruthy()
    expect(t.kind).toBe('event')
    expect(t.rawId).toBe('memo')
  })

  it('reports the fire count as runCount, and no clock state', () => {
    const t = eventToTrigger(wire)
    expect(t.runCount).toBe(3)
    expect(t.lastRunTs).toBeNull()
    expect(t.lastStatus).toBeNull()
  })

  it('preserves event state, health, last error, and run metadata', () => {
    const t = eventToTrigger({
      ...wire, state: 'parked', health: 'parked', last_error: 'delivery unavailable',
      last_run_ts: 1786000000, last_run_status: 'ran',
    } as unknown as WireTrigger)
    expect([t.state, t.health, t.lastError]).toEqual(['parked', 'parked', 'delivery unavailable'])
    expect([t.lastRunTs, t.lastStatus]).toEqual([1786000000, 'ran'])
  })

  it('carries only the ONE matcher its pattern reads', () => {
    expect(eventToTrigger(wire).eventMatcher).toBe('project.acme.*')
    const anyWrite = eventToTrigger({ ...wire, pattern: 'MemoryUpdate' } as unknown as WireTrigger)
    expect(eventPatternMeta('MemoryUpdate').matcher).toBeNull()
    expect(anyWrite.eventMatcher).toBe('')
  })

  it('passes the server read_only verdict through, like its siblings', () => {
    expect(eventToTrigger(wire).readOnly).toBe(false)
    const foreign = eventToTrigger({ ...wire, read_only: true, author: 'alice' } as unknown as WireTrigger)
    expect(foreign.readOnly).toBe(true)
    expect(foreign.author).toBe('alice')
  })

  it('does NOT set `hook`, which would open the wrong inspector', () => {
    const t = eventToTrigger(wire)
    expect(t.hook).toBeUndefined()
    expect(t.store).toBeUndefined()
    expect(t.event).toBeTruthy()
  })
})

describe('triggerStatusMeta', () => {
  it('keeps a healthy active trigger that has not run in the never-run state', () => {
    const t = scheduleToTrigger({
      id: 'clock:new', name: 'New', message: '', enabled: true, schedule: 'hourly',
      health: 'ok', state: 'active',
    } as ScheduleJob)
    expect(triggerStatusMeta(t).label).toBe('never run')
  })

  it('lets lifecycle state and unhealthy rollups outrank a prior run outcome', () => {
    const t = eventToTrigger({
      kind: 'event', id: 'event:x', raw_id: 'x', name: 'X', enabled: true,
      action: { provider: 'notify', config: {} }, state: 'parked', health: 'parked',
      last_run_ts: 1786000000, last_run_status: 'ran', last_error: 'safe reason',
    } as WireTrigger)
    expect(triggerStatusMeta(t)).toMatchObject({ label: 'parked', reason: 'safe reason' })
  })
})
