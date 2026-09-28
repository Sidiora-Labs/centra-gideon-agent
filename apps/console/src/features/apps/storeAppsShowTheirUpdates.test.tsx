import { describe, expect, it } from 'vitest'
import { appUpdateSource } from './AppsSection'

describe('Store app update source', () => {
  it('prefers the version discovered by the Store and otherwise starts from the installed source', () => {
    expect(appUpdateSource({ latestSource: 'https://example.invalid/new.git#app', updateSource: 'https://example.invalid/installed.git#app' }))
      .toBe('https://example.invalid/new.git#app')
    expect(appUpdateSource({ updateSource: 'https://example.invalid/installed.git#app' }))
      .toBe('https://example.invalid/installed.git#app')
    expect(appUpdateSource({})).toBe('')
  })
})
