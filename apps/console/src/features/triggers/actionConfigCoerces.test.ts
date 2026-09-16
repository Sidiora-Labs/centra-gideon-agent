
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { coerceActionConfig } from './ActionConfig'
import type { ActionProvider } from '../../shared/data/api'

const createTask = {
  name: 'create-task',
  display_name: 'Create Task',
  supports_blocking: false,
  settingsSchema: {
    type: 'object',
    required: ['title_template'],
    properties: {
      title_template: { type: 'string' },
      priority: { type: 'string', enum: ['low', 'high'] },
      labels: { type: 'array', items: { type: 'string' } },
    },
  },
} as unknown as ActionProvider

describe('coerceActionConfig', () => {
  it('parses a JSON array field into a real array', () => {
    const { config, error } = coerceActionConfig([createTask], 'create-task', {
      title_template: 'Market prep', labels: '["market","prep"]',
    })
    expect(error).toBeUndefined()
    expect(config.labels).toEqual(['market', 'prep'])
    expect(config.title_template).toBe('Market prep')
  })

  it('REFUSES the exact input from the issue instead of storing the string', () => {
    const { config, error } = coerceActionConfig([createTask], 'create-task', {
      title_template: 'Market prep', labels: 'market, prep',
    })
    expect(error).toBe('labels: invalid JSON')
    expect(config.labels).toBe('market, prep')
  })

  it('leaves the fields that always worked exactly as they were', () => {
    const { config, error } = coerceActionConfig([createTask], 'create-task', {
      title_template: 'Prep for $now', priority: 'high',
    })
    expect(error).toBeUndefined()
    expect(config).toEqual({ title_template: 'Prep for $now', priority: 'high' })
  })

  it('passes the config through untouched for an unknown provider', () => {
    const cfg = { anything: 'at all' }
    expect(coerceActionConfig([createTask], 'not-installed', cfg)).toEqual({ config: cfg })
  })
})

describe('every surface that edits an action config coerces before saving', () => {
  const DIR = join(process.cwd(), "src/features/triggers")

  const consumers = readdirSync(DIR)
    .filter((f) => /\.tsx$/.test(f) && !/\.test\./.test(f))
    .filter((f) => statSync(join(DIR, f)).isFile())
    .filter((f) => /<ActionConfig\b/.test(readFileSync(join(DIR, f), 'utf8')))

  it('found the consumers it is supposed to be checking', () => {
    expect(consumers.sort()).toEqual(['LifecycleDetail.tsx', 'TriggerCreatePage.tsx'])
  })

  it.each(consumers)('%s calls coerceActionConfig', (file) => {
    const source = readFileSync(join(DIR, file), 'utf8')
    expect(source).toMatch(/coerceActionConfig\s*\(/)
    expect(source).toMatch(/coerced\.error/)
  })

  it.each(consumers)('%s sends the coerced config, not the raw form state', (file) => {
    const source = readFileSync(join(DIR, file), 'utf8')
    expect(source).not.toMatch(/\bconfig:\s*config\b/)
    expect(source).not.toMatch(/provider_config:\s*config\b/)
    expect(source).not.toMatch(/\{\s*provider,\s*config\s*\}/)
  })
})
