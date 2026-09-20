import { useCallback, useRef, useState } from 'react'
import type { AppInstallResult, SkillInstallResult, AppScanReport } from './api'

export interface GuardedResult {
  ok: boolean
  needsConsent: boolean
  scan: AppScanReport | null
  error?: string
  clientInstall?: { shell?: string; postInstall?: string } | null
  restartRequired?: boolean
  fixPrompt?: string
}

export function terminalRefusalReason(r: GuardedResult | null | undefined): string {
  if (!r) return ''
  if (r.scan?.signature?.state === 'invalid') {
    return r.scan.signature.reason
      ? `This bundle's signature is invalid — ${r.scan.signature.reason}. It cannot be installed.`
      : "This bundle's signature is invalid. It cannot be installed."
  }
  if (r.scan?.verdict === 'dangerous') {
    return 'The security scanner flagged dangerous content. This app cannot be installed.'
  }
  return ''
}

export function isBlockingResult(r: GuardedResult | null | undefined): boolean {
  if (!r) return false
  return !!(r.needsConsent || terminalRefusalReason(r) || r.clientInstall)
}

export function guardedFromApp(r: AppInstallResult): GuardedResult {
  return { ok: r.ok, needsConsent: !!r.needs_consent, scan: r.scan, error: r.error,
            clientInstall: r.needs_client_install ? (r.client_install ?? {}) : null,
            restartRequired: !!r.restart_required,
            fixPrompt: r.fix_prompt || undefined }
}

export function guardedFromSkill(r: SkillInstallResult): GuardedResult {
  return { ok: !!r.ok, needsConsent: !!r.overridable, scan: r.scan ?? null, error: r.error }
}

export interface GuardedInstall {
  busy: boolean
  blocked: GuardedResult | null
  error: string | null
  fixPrompt: string | null
  install: () => Promise<GuardedResult | null>
  confirmInstall: () => Promise<GuardedResult | null>
  reset: () => void
}

export function useGuardedInstall(run: (confirm: boolean) => Promise<GuardedResult>): GuardedInstall {
  const [busy, setBusy] = useState(false)
  const [blocked, setBlocked] = useState<GuardedResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fixPrompt, setFixPrompt] = useState<string | null>(null)
  const runRef = useRef(run)
  runRef.current = run

  const attempt = useCallback(async (confirm: boolean): Promise<GuardedResult | null> => {
    setBusy(true)
    setError(null)
    setFixPrompt(null)
    if (!confirm) setBlocked(null)
    try {
      const r = await runRef.current(confirm)
      if (r.ok) {
        setBlocked(null)
        if (r.restartRequired) {
          window.dispatchEvent(new CustomEvent('ne:toast', { detail: {
            level: 'info',
            message: 'Installed — restart the gateway for this app to fully take effect.',
          }}))
        }
        return r
      }
      if (isBlockingResult(r)) { setBlocked(r); return r }
      setError(r.error || 'install failed')
      if (r.fixPrompt) setFixPrompt(r.fixPrompt)
      return r
    } catch (e) {
      setError(String((e as Error)?.message || e))
      return null
    } finally {
      setBusy(false)
    }
  }, [])

  const install = useCallback(() => attempt(false), [attempt])
  const confirmInstall = useCallback(() => attempt(true), [attempt])
  const reset = useCallback(() => { setBlocked(null); setError(null); setFixPrompt(null) }, [])

  return { busy, blocked, error, fixPrompt, install, confirmInstall, reset }
}
