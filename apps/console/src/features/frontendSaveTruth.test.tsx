import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { requireWriteAccepted } from '../shared/data/api'

const source = (path: string) => readFileSync(join(process.cwd(), 'src', path), 'utf8')

describe('frontend save and refusal truth', () => {
  it('turns an application-level refusal into a rejected write', () => {
    expect(() => requireWriteAccepted({ ok: false, error: 'read only' })).toThrow('read only')
    expect(requireWriteAccepted({ ok: true, value: 1 })).toEqual({ ok: true, value: 1 })
  })

  it('does not mark a structured document clean without a newer confirmed version', () => {
    const api = source('shared/data/api.ts')
    expect(api).toMatch(/result\.version <= previousVersion/)
    expect(api).toMatch(/Your edits are still marked unsaved/)
  })

  it('keeps workflow refusals visible after the action settles', () => {
    const detail = source('features/workflows/WorkflowRunDetail.tsx')
    expect(detail).toMatch(/setActionError\(message\)/)
    expect(detail).toMatch(/actionError && <InlineError/)
    expect(detail).toMatch(/throw new Error\(res\.issues\?\.\[0\]\?\.message/)
  })

  it('renders a retryable dashboard error instead of an empty recent-chat result', () => {
    const dashboard = source('features/dashboard/DashboardPage.tsx')
    expect(dashboard).toMatch(/sessionsError && \(/)
    expect(dashboard).toMatch(/InlineError icon onRetry=\{refreshSessions\}/)
  })

  it('keeps the last workflow questions when either refresh read fails', () => {
    const detail = source('features/workflows/WorkflowRunDetail.tsx')
    expect(detail).toMatch(/Promise\.allSettled/)
    expect(detail).toMatch(/continuations\.status === 'fulfilled'\) setConts/)
    expect(detail).toMatch(/loadError && <InlineError[^>]*onRetry=\{refetch\}/)
  })

  it('reconciles settings after success or refusal without swallowing the outcome', () => {
    const settings = source('features/settings/settingsWidgets.tsx')
    expect(settings).toMatch(/requireWriteAccepted\(await fn\(\)\)/)
    expect(settings).toMatch(/finally \{\s*invalidateSpecs\(affects\)/)
    expect(settings).toMatch(/catch \(e\)[\s\S]{0,200}?return false/)
  })
})
