import { useCallback, useRef, useState } from 'react'
import type { AppInstallResult, AppPreviewResult, AppDisclosure, SkillInstallResult, AppScanReport } from './api'

export interface GuardedResult {
  ok: boolean
  name?: string
  needsConsent: boolean
  scan: AppScanReport | null
  error?: string
  errorCode?: string
  clientInstall?: { shell?: string; postInstall?: string } | null
  restartRequired?: boolean
  restartPackages?: string[]
  fixPrompt?: string
  hooks?: Array<{ name: string; event: string; provider: string }>
  review?: AppDisclosure
  previousReview?: AppDisclosure | null
  reviewDigest?: string
  registry?: string | null
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
  const hooks = r.hooks
  const error = typeof r.error === 'string' ? r.error : r.error?.message || r.error?.code || ''
  const errorCode = typeof r.error === 'object' && r.error ? r.error.code : undefined
  return { ok: r.ok, name: r.name, needsConsent: !!r.needs_consent, scan: r.scan, error, errorCode,
            clientInstall: r.needs_client_install ? (r.client_install ?? {}) : null,
            restartRequired: !!r.restart_required,
            restartPackages: r.restart_packages ?? [],
            fixPrompt: r.fix_prompt || undefined, hooks, review: r.review,
            previousReview: r.previous_review, reviewDigest: r.review_digest, registry: r.registry }
}

export function guardedFromPreview(r: AppPreviewResult): GuardedResult {
  const error = typeof r.error === 'string' ? r.error : r.error?.message || r.error?.code || ''
  const errorCode = typeof r.error === 'object' && r.error ? r.error.code : undefined
  return { ok: r.ok, name: r.name, needsConsent: !!r.needs_consent, scan: r.scan, error, errorCode,
    clientInstall: r.needs_client_install ? (r.client_install ?? {}) : null,
    fixPrompt: r.fix_prompt || undefined, hooks: r.hooks,
    review: r.review, previousReview: r.previous_review, reviewDigest: r.review_digest, registry: r.registry }
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

export function useGuardedInstall(run: (confirm: boolean) => Promise<GuardedResult>): GuardedInstall
export function useGuardedInstall(preview: () => Promise<GuardedResult>, commit: (reviewDigest: string, registry?: string | null, name?: string) => Promise<GuardedResult>): GuardedInstall
export function useGuardedInstall(
  runOrPreview: ((confirm: boolean) => Promise<GuardedResult>) | (() => Promise<GuardedResult>),
  commit?: (reviewDigest: string, registry?: string | null, name?: string) => Promise<GuardedResult>,
): GuardedInstall {
  const [busy, setBusy] = useState(false)
  const [blocked, setBlocked] = useState<GuardedResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fixPrompt, setFixPrompt] = useState<string | null>(null)
  const runRef = useRef(runOrPreview)
  runRef.current = runOrPreview
  const commitRef = useRef(commit)
  commitRef.current = commit

  const finish = (r: GuardedResult): GuardedResult => {
    if (r.ok) {
      setBlocked(null)
      if (r.restartRequired) {
        const packages = r.restartPackages?.length
          ? ` Changed packages: ${r.restartPackages.join(', ')}.`
          : ''
        window.dispatchEvent(new CustomEvent('ne:toast', { detail: {
          level: 'info',
          message: `Installed — restart the gateway for this app to fully take effect.${packages}`,
        }}))
      }
      return r
    }
    if (isBlockingResult(r)) { setBlocked(r); return r }
    setError(r.error || 'install failed')
    if (r.fixPrompt) setFixPrompt(r.fixPrompt)
    return r
  }

  const attempt = useCallback(async (confirm: boolean): Promise<GuardedResult | null> => {
    setBusy(true)
    setError(null)
    setFixPrompt(null)
    setBlocked(null)
    try {
      if (!commitRef.current) {
        return finish(await (runRef.current as (confirm: boolean) => Promise<GuardedResult>)(confirm))
      }
      if (!confirm) {
        const reviewed = await (runRef.current as () => Promise<GuardedResult>)()
        if (reviewed.error && !reviewed.review) return finish(reviewed)
        if (terminalRefusalReason(reviewed) || reviewed.clientInstall || reviewed.needsConsent) {
          return finish(reviewed)
        }
        if (!reviewed.reviewDigest) {
          return finish({ ...reviewed, ok: false, error: 'The review expired. Review this app again before installing.' })
        }
        return finish(await commitRef.current(reviewed.reviewDigest, reviewed.registry, reviewed.name))
      }
      const current = blocked
      if (!current?.reviewDigest) {
        return finish({ ok: false, needsConsent: false, scan: null, error: 'Review this app before installing.' })
      }
      return finish(await commitRef.current(current.reviewDigest, current.registry, current.name))
    } catch (e) {
      setError(String((e as Error)?.message || e))
      return null
    } finally {
      setBusy(false)
    }
  }, [blocked])

  const install = useCallback(() => attempt(false), [attempt])
  const confirmInstall = useCallback(() => attempt(true), [attempt])
  const reset = useCallback(() => { setBlocked(null); setError(null); setFixPrompt(null) }, [])

  return { busy, blocked, error, fixPrompt, install, confirmInstall, reset }
}
