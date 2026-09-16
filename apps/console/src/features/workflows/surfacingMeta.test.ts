import { describe, expect, it } from 'vitest'
import type { WorkflowSurfacingRow } from '../../shared/data/api'
import {
  cadenceLabel,
  canResolveNode,
  composerChip,
  findingsByDef,
  freshnessLook,
  modeLook,
  needsAttention,
  packChips,
  tokenForNode,
  tracksCadence,
} from './surfacingMeta'
import { missingRequired, validateDeepLinkParams } from './templateStart'

const row = (over: Partial<WorkflowSurfacingRow> = {}): WorkflowSurfacingRow => ({
  name: 'backup',
  provider: 'native',
  surface_mode: 'passive',
  summary: '',
  when_to_use: '',
  cadence_days: 0,
  escalation: 'manual',
  packs: [],
  guided: false,
  freshness: 'fresh',
  overdue: false,
  last_completed_at: 0,
  hands_off_to: [],
  ...over,
})

describe('freshness presentation', () => {
  it('gives never_run its OWN look, not the stale one', () => {
    expect(freshnessLook('never_run').label).toBe('Never run')
    expect(freshnessLook('never_run').tone).not.toBe(freshnessLook('stale').tone)
  })

  it('separates overdue from stale', () => {
    expect(freshnessLook('overdue').tone).not.toBe(freshnessLook('stale').tone)
  })

  it('falls back safely for a band this build does not know', () => {
    const look = freshnessLook('quantum')
    expect(look.label).toBe('quantum')
    expect(look.icon).toBeTruthy()
  })

  it('never returns an empty label', () => {
    expect(freshnessLook('').label).toBe('Unknown')
  })
})

describe('surface-mode presentation', () => {
  it('names all three modes distinctly', () => {
    const labels = ['off', 'passive', 'suggest'].map((m) => modeLook(m).label)
    expect(new Set(labels).size).toBe(3)
  })

  it('reads an UNKNOWN mode as off', () => {
    expect(modeLook('vibes')).toEqual(modeLook('off'))
  })
})

describe('cadence line', () => {
  it('is EMPTY for a def with no cadence', () => {
    expect(tracksCadence(row())).toBe(false)
    expect(cadenceLabel(row())).toBe('')
  })

  it('singularizes one day', () => {
    expect(cadenceLabel(row({ cadence_days: 1 }))).toBe('Every day')
  })

  it('NAMES auto escalation rather than implying it', () => {
    const label = cadenceLabel(row({ cadence_days: 7, escalation: 'auto' }))
    expect(label).toContain('files a task')
    expect(cadenceLabel(row({ cadence_days: 7 }))).not.toContain('files a task')
  })
})

describe('composer chip', () => {
  it('gives an OFF def no chip at all', () => {
    expect(composerChip(row({ surface_mode: 'off' }))).toBeNull()
  })

  it('gives a passive def a chip with NO run affordance', () => {
    const chip = composerChip(row({ surface_mode: 'passive' }))
    expect(chip?.runnable).toBe(false)
  })

  it('gives a suggest def a runnable chip', () => {
    expect(composerChip(row({ surface_mode: 'suggest' }))?.runnable).toBe(true)
  })

  it('names the def so the user can tell WHICH sop fired', () => {
    expect(composerChip(row({ name: 'deploy' }))?.label).toContain('deploy')
  })

  it('carries the summary as the hover preview', () => {
    expect(composerChip(row({ summary: 'Backs things up' }))?.preview).toBe('Backs things up')
  })
})

describe('doctor findings', () => {
  it('groups by def so a finding renders beside its row', () => {
    const grouped = findingsByDef([
      { name: 'ghost', code: 'no_channel', detail: 'x' },
      { name: 'ghost', code: 'shadowed', detail: 'y' },
      { name: 'other', code: 'no_channel', detail: 'z' },
    ])
    expect(grouped.ghost).toHaveLength(2)
    expect(grouped.other).toHaveLength(1)
  })

  it('drops a finding with no def name rather than creating an empty bucket', () => {
    expect(findingsByDef([{ name: '', code: 'x', detail: '' }])).toEqual({})
  })

  it('handles an absent list', () => {
    expect(findingsByDef(undefined as never)).toEqual({})
  })
})

describe('attention', () => {
  it('flags an overdue row', () => {
    expect(needsAttention(row({ overdue: true }), 0)).toBe(true)
  })

  it('flags a row with a doctor finding even when it is fresh', () => {
    expect(needsAttention(row(), 1)).toBe(true)
  })

  it('leaves a healthy row alone', () => {
    expect(needsAttention(row(), 0)).toBe(false)
  })
})

describe('pack chips', () => {
  it('renders declared packs', () => {
    expect(packChips(row({ packs: ['ci', 'python-project'] }))).toEqual(['ci', 'python-project'])
  })

  it('renders NOTHING for a def in no pack', () => {
    expect(packChips(row())).toEqual([])
  })

  it('drops empty entries', () => {
    expect(packChips(row({ packs: ['', 'ci'] }))).toEqual(['ci'])
  })
})

describe('deep-link params are ALLOWLISTED against the schema', () => {
  const inputs = { env: { required: true }, version: { required: false } }

  it('accepts a declared param', () => {
    const { accepted } = validateDeepLinkParams({ env: 'staging' }, inputs)
    expect(accepted).toEqual({ env: 'staging' })
  })

  it('REJECTS an undeclared param instead of passing it through', () => {
    const { accepted, rejected } = validateDeepLinkParams({ env: 'prod', evil: '1' }, inputs)
    expect(accepted).toEqual({ env: 'prod' })
    expect(rejected).toEqual(['evil'])
  })

  it('REPORTS rejections so a stale link can explain itself', () => {
    expect(validateDeepLinkParams({ oldName: 'x' }, inputs).rejected).toEqual(['oldName'])
  })

  it('does not report `template` as a rejection', () => {
    expect(validateDeepLinkParams({ template: 'backup' }, inputs).rejected).toEqual([])
  })

  it('drops an EMPTY value rather than pre-filling a blank', () => {
    expect(validateDeepLinkParams({ env: '' }, inputs).accepted).toEqual({})
  })

  it('accepts a real URLSearchParams', () => {
    const params = new URLSearchParams('template=backup&env=prod&junk=1')
    const { accepted, rejected } = validateDeepLinkParams(params, inputs)
    expect(accepted).toEqual({ env: 'prod' })
    expect(rejected).toEqual(['junk'])
  })

  it('rejects EVERYTHING for a template that declares no inputs', () => {
    expect(validateDeepLinkParams({ anything: '1' }, undefined).rejected).toEqual(['anything'])
  })
})

describe('required inputs are RE-DERIVED, not trusted', () => {
  const inputs = { env: { required: true }, version: { required: false } }

  it('reports a missing required input', () => {
    expect(missingRequired({}, inputs)).toEqual(['env'])
  })

  it('is satisfied once the required input is present', () => {
    expect(missingRequired({ env: 'prod' }, inputs)).toEqual([])
  })

  it('does not demand optional inputs', () => {
    expect(missingRequired({ env: 'prod' }, inputs)).not.toContain('version')
  })

  it('is empty for a template with no inputs', () => {
    expect(missingRequired({}, undefined)).toEqual([])
  })
})

describe('node → resume-token join', () => {
  const conts = [
    { node_id: 'approve', resume_token: 'tok-a' },
    { node_id: 'publish', resume_token: 'tok-p' },
  ]

  it('finds the token for a node', () => {
    expect(tokenForNode(conts, 'publish')).toBe('tok-p')
  })

  it('returns EMPTY for a node with nothing pending', () => {
    expect(tokenForNode(conts, 'other')).toBe('')
  })

  it('ignores an EXPIRED continuation', () => {
    expect(tokenForNode([{ node_id: 'a', resume_token: 't', expired: true }], 'a')).toBe('')
  })

  it('handles an absent list', () => {
    expect(tokenForNode(undefined as never, 'a')).toBe('')
  })

  it('gates BOTH verbs on the same answer', () => {
    expect(canResolveNode(conts, 'approve')).toBe(true)
    expect(canResolveNode(conts, 'gone')).toBe(false)
  })
})
