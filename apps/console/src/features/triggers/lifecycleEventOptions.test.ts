import { describe, it, expect } from 'vitest'
import { lifecycleEventOptions } from './triggerMeta'
import type { TriggerVariables } from '../../shared/data/api'


const ev = (event: string, label: string, dormant = false) => ({ event, label, desc: `${label} desc`, dormant, vars: [] })

const cat = (lifecycle: ReturnType<typeof ev>[]): TriggerVariables =>
  ({ lifecycle, schedule: [], app_sources: [] } as unknown as TriggerVariables)

describe('lifecycleEventOptions', () => {
  it('emits NO group at all while nothing is dormant — todays measured shape', () => {
    const opts = lifecycleEventOptions(cat([ev('SessionStart', 'Session start'), ev('Stop', 'Stop')]))
    expect(opts.map((o) => o.value)).toEqual(['SessionStart', 'Stop'])
    expect(opts.every((o) => o.group === undefined)).toBe(true)
    expect(opts.some((o) => /never fires/.test(o.label))).toBe(false)
  })

  it('groups live-first the moment ONE event is dormant', () => {
    const opts = lifecycleEventOptions(cat([
      ev('Dead', 'Dead event', true),
      ev('SessionStart', 'Session start'),
      ev('AlsoDead', 'Also dead', true),
      ev('Stop', 'Stop'),
    ]))
    expect(opts.map((o) => o.value)).toEqual(['SessionStart', 'Stop', 'Dead', 'AlsoDead'])
    expect(opts.map((o) => o.group)).toEqual([
      'Live events', 'Live events',
      'Advanced — nothing fires these yet', 'Advanced — nothing fires these yet',
    ])
  })

  it('marks a dormant event inline as well as grouping it', () => {
    const opts = lifecycleEventOptions(cat([ev('Dead', 'Dead event', true), ev('Stop', 'Stop')]))
    expect(opts.find((o) => o.value === 'Dead')?.label).toBe('Dead event · never fires')
    expect(opts.find((o) => o.value === 'Stop')?.label).toBe('Stop')
  })

  it('keeps dormant events PICKABLE rather than hiding them', () => {
    const opts = lifecycleEventOptions(cat([ev('Dead', 'Dead event', true)]))
    expect(opts).toHaveLength(1)
    expect(opts[0].value).toBe('Dead')
  })

  it('carries every event through — grouping is presentation, not a filter', () => {
    const input = Array.from({ length: 15 }, (_, i) => ev(`E${i}`, `Event ${i}`, i % 3 === 0))
    const opts = lifecycleEventOptions(cat(input))
    expect(opts).toHaveLength(15)
    expect(new Set(opts.map((o) => o.value)).size).toBe(15)
  })

  it('is empty and unthrowing while the catalog is still loading', () => {
    expect(lifecycleEventOptions(null)).toEqual([])
  })
})
