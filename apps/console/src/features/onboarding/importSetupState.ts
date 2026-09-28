import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import { api, type OnboardingImportItem, type OnboardingImportJob, type OnboardingImportReport, type OnboardingImportScan } from '../../shared/data/api'
import { useVisiblePoll } from '../../shared/data/useVisiblePoll'

export function writableImportItem(item: OnboardingImportItem): boolean {
  return item.state ? item.state === 'new' : !item.existing
}
export function selectedImportFingerprints(items: OnboardingImportItem[], picked: Record<string, boolean>): string[] {
  return [...new Set(items.filter(item => writableImportItem(item) && picked[item.fingerprint]).map(item => item.fingerprint))]
}
type ImportState = { scan: OnboardingImportScan | null; scanError: unknown; pickedItems: Record<string, boolean>; busy: boolean; job: OnboardingImportJob | null; report: OnboardingImportReport | null; failure: string }
const initial: ImportState = { scan: null, scanError: null, pickedItems: {}, busy: false, job: null, report: null, failure: '' }
export function useSetupImport() {
  const [state, change] = useReducer((state: ImportState, patch: Partial<ImportState> | ((state: ImportState) => ImportState)) =>
    typeof patch === 'function' ? patch(state) : ({ ...state, ...patch }), initial)
  const generation = useRef(0)
  const writing = useRef(false)
  const refreshScan = useCallback(() => {
    const version = generation.current
    void api.onboardingImportScan().then(scan => {
      if (version !== generation.current) return
      change(state => {
        const items = scan.sources.flatMap(source => source.items)
        const picked = { ...state.pickedItems }
        for (const item of items) if (!(item.fingerprint in picked)) picked[item.fingerprint] = writableImportItem(item) && item.preselected !== false
        return { ...state, scan, pickedItems: picked }
      })
    }).catch(scanError => { if (version === generation.current) change({ scanError }) })
  }, [])
  const load = useCallback(() => {
    const version = ++generation.current
    change({ scan: null, scanError: null })
    void api.onboardingImportScan().then(scan => {
      if (version !== generation.current) return
      const items = scan.sources.flatMap(source => source.items)
      change({ scan, pickedItems: Object.fromEntries(items.map(item => [item.fingerprint, writableImportItem(item) && item.preselected !== false])) })
    }).catch(scanError => { if (version === generation.current) change({ scanError }) })
    void api.onboardingImportJob().then(({ job }) => {
      if (version !== generation.current || !job) return
      change({ job, busy: job.status === 'running', report: job.status === 'running' ? null : job.report })
    }).catch(() => {})
  }, [])
  useEffect(() => { load(); return () => { generation.current += 1 } }, [load])
  const pollJob = useCallback(() => {
    const version = generation.current
    void api.onboardingImportJob().then(({ job }) => {
      if (version !== generation.current || !job) return
      change({ job, busy: job.status === 'running', report: job.status === 'running' ? null : job.report })
    }).catch(failure => { if (version === generation.current) change({ failure: failure instanceof Error ? failure.message : 'Could not refresh import progress.' }) })
  }, [])
  const reading = !!state.scan?.sources.some(source => source.reading && source.reading.read < source.reading.of)
  useVisiblePoll(refreshScan, reading ? 1200 : null)
  useVisiblePoll(pollJob, state.job?.status === 'running' ? 900 : null)
  const projection = useMemo(() => {
    const detected = state.scan?.sources.filter(source => source.detected) ?? []
    const fingerprints = selectedImportFingerprints(detected.flatMap(source => source.items), state.pickedItems)
    return { detected, fingerprints, nothingPicked: !fingerprints.length }
  }, [state.scan, state.pickedItems])
  const run = async (requested?: string[]) => {
    const fingerprints = requested ?? projection.fingerprints
    if (writing.current || !fingerprints.length) return
    const version = generation.current
    writing.current = true; change({ busy: true, failure: '', report: null })
    try {
      const { job } = await api.runOnboardingImport({ fingerprints, accepted: Object.fromEntries(projection.detected.flatMap(source => source.items).filter(item => fingerprints.includes(item.fingerprint) && item.scan?.verdict === "warning").map(item => [item.fingerprint, item.scan!.consent])) })
      if (version === generation.current) change({ job, busy: job.status === 'running', report: job.status === 'running' ? null : job.report })
    } catch (failure) { if (version === generation.current) change({ busy: false, failure: failure instanceof Error ? failure.message : 'The import could not be completed.' }) }
    finally { writing.current = false }
  }
  const stop = async () => {
    if (!state.job || state.job.status !== 'running') return
    try { const { job } = await api.stopOnboardingImport(); change({ job }) }
    catch (failure) { change({ failure: failure instanceof Error ? failure.message : 'Could not stop the import.' }) }
  }
  const resumeRemaining = () => {
    const remaining = state.report?.not_reached ?? []
    if (remaining.length) void run(remaining)
  }
  return { ...state, ...projection, load, run, stop, resumeRemaining, reading,
    pickItems: (items: OnboardingImportItem[], value: boolean) => change({ pickedItems: { ...state.pickedItems, ...Object.fromEntries(items.filter(writableImportItem).map(item => [item.fingerprint, value])) } }),
  }
}
