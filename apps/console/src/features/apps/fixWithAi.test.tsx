import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { guardedFromApp } from '../../shared/data/useGuardedInstall'
import type { AppInstallResult } from '../../shared/data/api'


const launchChat = vi.fn()
vi.mock('../../app/shell/appSdk', () => ({ launchChat: (...a: unknown[]) => launchChat(...a) }))

import { FixWithAiButton } from './AppsSection'

const FENCED = '<untrusted_content source=app_install_log:broken source_type=app_install_log>\nboom\n</untrusted_content>'

describe('guardedFromApp — fix_prompt passthrough', () => {
  it('carries a failed install\'s fix_prompt into the guarded result', () => {
    const raw: AppInstallResult = {
      ok: false, name: 'broken', error: 'onInstall hook exited 7', needs_consent: false,
      scan: null, log_excerpt: 'boom', fix_prompt: `debug this:\n\n${FENCED}`,
    }
    expect(guardedFromApp(raw).fixPrompt).toBe(`debug this:\n\n${FENCED}`)
  })

  it('leaves fixPrompt undefined on a successful install (empty string → undefined)', () => {
    const raw: AppInstallResult = {
      ok: true, name: 'good', error: '', needs_consent: false, scan: null,
      log_excerpt: '', fix_prompt: '',
    }
    expect(guardedFromApp(raw).fixPrompt).toBeUndefined()
  })
})

describe('FixWithAiButton', () => {
  beforeEach(() => launchChat.mockReset())

  it('renders nothing when there is no fix prompt', () => {
    const { container } = render(<FixWithAiButton fixPrompt={null} />)
    expect(container.querySelector('button')).toBeNull()
  })

  it('renders the button and passes the fenced prompt to launchChat on click', () => {
    const prompt = `debug this:\n\n${FENCED}`
    const { getByRole } = render(<FixWithAiButton fixPrompt={prompt} />)
    const btn = getByRole('button', { name: /fix with ai/i })
    fireEvent.click(btn)
    expect(launchChat).toHaveBeenCalledTimes(1)
    expect(launchChat).toHaveBeenCalledWith({ prompt })
  })
})
