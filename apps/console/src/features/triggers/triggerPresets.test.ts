import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { emptyDraft } from '../schedule/ScheduleForm'
import {
  TRIGGER_PRESETS, cadenceCron, cadenceLabel, findTriggerPreset, prefillDraft, type Cadence,
} from './triggerPresets'


function providerSchema(provider: string): { required: string[]; properties: Record<string, unknown> } {
  const p = join(__dirname, "../../../../../runtime/gideon/extensions/apps/native", `${provider}-action`, 'app.json')
  const manifest = JSON.parse(readFileSync(p, 'utf8'))
  const schema = manifest?.provider?.settingsSchema ?? {}
  return { required: schema.required ?? [], properties: schema.properties ?? {} }
}

describe('the Triggers preset catalog', () => {
  it('offers a handful of presets with unique ids', () => {
    expect(TRIGGER_PRESETS.length).toBeGreaterThanOrEqual(4)
    expect(new Set(TRIGGER_PRESETS.map((p) => p.id)).size).toBe(TRIGGER_PRESETS.length)
  })

  it('declares each fact once — the card id/title ARE the prefill id/name', () => {
    for (const p of TRIGGER_PRESETS) {
      expect(p.prefill.id, `${p.id}: the prefill carries the id the URL will`).toBe(p.id)
      expect(p.prefill.name, `${p.id}: the saved trigger is named after the card`).toBe(p.title)
      expect(p.summary, `${p.id}: the summary line is DERIVED from the cadence`).toBe(cadenceLabel(p.prefill.cadence))
    }
  })

  it('fills every REQUIRED field of the provider it names', () => {
    for (const p of TRIGGER_PRESETS) {
      const { required } = providerSchema(p.prefill.provider)
      expect(required.length, `${p.prefill.provider} manifest declares required fields`).toBeGreaterThan(0)
      for (const key of required) {
        const v = p.prefill.config[key]
        expect(typeof v, `${p.id}: required "${key}" must be seeded`).toBe('string')
        expect(String(v).trim(), `${p.id}: required "${key}" must not be blank`).not.toBe('')
      }
    }
  })

  it('sets no config key the provider does not declare', () => {
    for (const p of TRIGGER_PRESETS) {
      const { properties } = providerSchema(p.prefill.provider)
      for (const key of Object.keys(p.prefill.config))
        expect(Object.keys(properties), `${p.id}: "${key}" is not in ${p.prefill.provider}'s schema`).toContain(key)
    }
  })

  it('names only core-native providers (no preset needs an installed app)', () => {
    for (const p of TRIGGER_PRESETS)
      expect(['invoke-agent', 'notify', 'bash', 'run-script', 'send-message', 'create-task', 'run-prompt'])
        .toContain(p.prefill.provider)
  })
})

describe('cadence → the two things derived from it', () => {
  it('builds a five-field cron for every clock cadence', () => {
    for (const p of TRIGGER_PRESETS) {
      const c = p.prefill.cadence
      if (c.kind === 'everyHours') continue
      const cron = cadenceCron(c)
      expect(cron.split(/\s+/), `${p.id}: "${cron}"`).toHaveLength(5)
      expect(cron, `${p.id}`).toMatch(/^\d{1,2} \d{1,2} \* \* (\*|\d|1-5)$/)
    }
  })

  it('puts the cadence, and nothing else, into the schedule draft', () => {
    const blank = emptyDraft()
    for (const p of TRIGGER_PRESETS) {
      const d = prefillDraft(p.prefill)
      const changed = Object.keys(blank).filter((k) => {
        const a = (d as unknown as Record<string, unknown>)[k]
        const b = (blank as unknown as Record<string, unknown>)[k]
        return JSON.stringify(a) !== JSON.stringify(b)
      })
      for (const k of changed)
        expect(['kind', 'cron', 'intervalValue', 'intervalUnit'], `${p.id} also changed "${k}"`).toContain(k)
    }
  })

  it('routes a clock cadence to cron and an interval cadence to every', () => {
    const daily = prefillDraft({ id: 'x', name: 'x', cadence: { kind: 'daily', hour: 6, minute: 30 }, provider: 'notify', config: {} })
    expect(daily.kind).toBe('cron')
    expect(daily.cron).toBe('30 6 * * *')

    const hourly = prefillDraft({ id: 'x', name: 'x', cadence: { kind: 'everyHours', hours: 4 }, provider: 'notify', config: {} })
    expect(hourly.kind).toBe('every')
    expect(hourly.intervalValue).toBe(4)
    expect(hourly.intervalUnit).toBe('h')
  })

  it('numbers weekdays the way cron does — 1 is Monday', () => {
    expect(cadenceLabel({ kind: 'weekly', weekday: 1, hour: 9, minute: 0 }, 'en-US')).toContain('Monday')
    expect(cadenceLabel({ kind: 'weekly', weekday: 0, hour: 9, minute: 0 }, 'en-US')).toContain('Sunday')
    expect(cadenceCron({ kind: 'weekly', weekday: 1, hour: 9, minute: 0 })).toBe('0 9 * * 1')
  })

  it('formats the clock and the weekday through the LOCALE, not frozen en-US copy', () => {
    const morning: Cadence = { kind: 'daily', hour: 8, minute: 0 }
    expect(cadenceLabel(morning, 'en-US')).toBe('Every day · 8:00 AM')
    expect(cadenceLabel(morning, 'de-DE')).toBe('Every day · 8:00')
    expect(cadenceLabel(morning, 'de-DE')).not.toMatch(/AM|PM/)
    const evening: Cadence = { kind: 'daily', hour: 20, minute: 0 }
    expect(cadenceLabel(evening, 'en-US')).toBe('Every day · 8:00 PM')
    expect(cadenceLabel(evening, 'de-DE')).toBe('Every day · 20:00')

    const monday: Cadence = { kind: 'weekly', weekday: 1, hour: 9, minute: 0 }
    expect(cadenceLabel(monday, 'de-DE')).toContain('Montag')
    expect(cadenceLabel(monday, 'ja-JP')).toContain('月曜日')
  })

  it('formats an afternoon hour without wrapping the day (the UTC weekday trap)', () => {
    expect(cadenceLabel({ kind: 'daily', hour: 23, minute: 0 }, 'en-US')).toBe('Every day · 11:00 PM')
    for (let wd = 0; wd < 7; wd++) {
      const label = cadenceLabel({ kind: 'weekly', weekday: wd, hour: 0, minute: 0 }, 'en-US')
      expect(label).toContain(['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][wd])
    }
  })

  it('says "Every hour" in the singular and counts hours otherwise', () => {
    expect(cadenceLabel({ kind: 'everyHours', hours: 1 })).toBe('Every hour')
    expect(cadenceLabel({ kind: 'everyHours', hours: 6 })).toBe('Every 6 hours')
    expect(cadenceCron({ kind: 'everyHours', hours: 6 })).toBe('')
  })
})

describe('findTriggerPreset — what keeps the expert path expert', () => {
  it('resolves a known id to its preset', () => {
    expect(findTriggerPreset('morning-briefing')?.title).toBe('Morning briefing')
  })

  it('returns null for an absent id — the blank create path', () => {
    expect(findTriggerPreset('')).toBeNull()
  })

  it('returns null for an unknown id rather than guessing one', () => {
    expect(findTriggerPreset('nope')).toBeNull()
    expect(findTriggerPreset('MORNING-BRIEFING')).toBeNull()
  })
})
