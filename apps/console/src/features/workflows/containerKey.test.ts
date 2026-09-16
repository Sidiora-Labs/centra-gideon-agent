import { describe, expect, it } from 'vitest'

import { KIND_TO_TEMPLATE, baseContainer, belongsToLoop, keysEquivalent, templateForKind } from './containerKey'

describe('stream-key equivalence', () => {
  it('treats a loop key and a run key for the same id as the same container', () => {
    expect(keysEquivalent('loop:abc', 'run:abc')).toBe(true)
    expect(keysEquivalent('loop:abc', 'workflow:run:abc')).toBe(true)
    expect(keysEquivalent('workflow:abc', 'abc')).toBe(true)
  })

  it('does not conflate different containers', () => {
    expect(keysEquivalent('loop:abc', 'loop:xyz')).toBe(false)
    expect(keysEquivalent('workflow:run:abc', 'run:xyz')).toBe(false)
  })

  it('never treats empty keys as equivalent', () => {
    expect(keysEquivalent('', '')).toBe(false)
    expect(keysEquivalent('loop:', 'run:')).toBe(false)
    expect(keysEquivalent(null, undefined)).toBe(false)
    expect(keysEquivalent('loop:abc', null)).toBe(false)
  })

  it('strips the longest prefix first', () => {
    expect(baseContainer('workflow:run:abc')).toBe('abc')
    expect(baseContainer('workflow:abc')).toBe('abc')
    expect(baseContainer('loop:abc')).toBe('abc')
    expect(baseContainer('abc')).toBe('abc')
  })

  it('tolerates whitespace and missing values', () => {
    expect(baseContainer('  loop:abc  ')).toBe('abc')
    expect(baseContainer(null)).toBe('')
    expect(baseContainer(undefined)).toBe('')
  })

  it('is reflexive and symmetric', () => {
    expect(keysEquivalent('loop:abc', 'loop:abc')).toBe(true)
    expect(keysEquivalent('run:abc', 'loop:abc')).toBe(keysEquivalent('loop:abc', 'run:abc'))
  })
})

describe('belongsToLoop — cockpit live-follow (R10c)', () => {
  it('matches the legacy hyphen worker key exactly', () => {
    expect(belongsToLoop('loop-abc', 'abc')).toBe(true)
  })

  it('matches a task-scoped sub-worker of the same loop', () => {
    expect(belongsToLoop('loop-abc-task7', 'abc')).toBe(true)
  })

  it('matches a coexistence run-scoped key for the same container', () => {
    expect(belongsToLoop('run:abc', 'abc')).toBe(true)
    expect(belongsToLoop('workflow:run:abc', 'abc')).toBe(true)
    expect(belongsToLoop('loop:abc', 'abc')).toBe(true)
  })

  it('does not match a different loop, even one whose id shares this prefix', () => {
    expect(belongsToLoop('loop-xyz', 'abc')).toBe(false)
    expect(belongsToLoop('loop-abcx', 'abc')).toBe(false)
    expect(belongsToLoop('run:xyz', 'abc')).toBe(false)
  })

  it('never matches on an empty key or loop id', () => {
    expect(belongsToLoop('', 'abc')).toBe(false)
    expect(belongsToLoop('loop-abc', '')).toBe(false)
    expect(belongsToLoop(null, 'abc')).toBe(false)
    expect(belongsToLoop('loop-abc', undefined)).toBe(false)
  })
})

describe('legacy loop-kind aliases', () => {
  it('resolves every legacy kind', () => {
    for (const kind of ['general', 'goal', 'code', 'design', 'research']) {
      expect(templateForKind(kind)).not.toBe('')
    }
  })

  it('resolves an unknown kind to nothing rather than a default', () => {
    expect(templateForKind('nonsense')).toBe('')
    expect(templateForKind('')).toBe('')
    expect(templateForKind(null)).toBe('')
    expect(templateForKind(undefined)).toBe('')
  })

  it('tolerates case and whitespace in stored references', () => {
    expect(templateForKind('GOAL')).toBe('goal-pursuit-open-ended')
    expect(templateForKind(' code ')).toBe('code-project')
  })

  it('reads a verify command as the verifiable variant', () => {
    expect(templateForKind('goal', { hasVerifyCommand: true })).toBe('goal-pursuit-verifiable')
    expect(templateForKind('goal')).toBe('goal-pursuit-open-ended')
  })

  it('lets an explicit variant win over the inferred one', () => {
    expect(templateForKind('goal', { variant: 'open-ended', hasVerifyCommand: true })).toBe(
      'goal-pursuit-open-ended',
    )
    expect(templateForKind('goal', { variant: 'verifiable' })).toBe('goal-pursuit-verifiable')
  })

  it('only special-cases goal', () => {
    expect(templateForKind('research', { hasVerifyCommand: true })).toBe('deep-research')
  })

  it('exposes the table for the picker', () => {
    expect(Object.keys(KIND_TO_TEMPLATE).sort()).toEqual([
      'code',
      'design',
      'general',
      'goal',
      'research',
    ])
  })
})
