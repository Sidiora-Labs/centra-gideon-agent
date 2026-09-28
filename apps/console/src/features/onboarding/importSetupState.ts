import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import { api, type OnboardingImportItem, type OnboardingImportReport, type OnboardingImportScan } from '../../shared/data/api'

export function writableImportItem(item: OnboardingImportItem): boolean {
  return item.state ? item.state === 'new' : !item.existing
}
export function selectedImportFingerprints(items: OnboardingImportItem[], picked: Record<string, boolean>): string[] {
  return [...new Set(items.filter(item => writableImportItem(item) && picked[item.fingerprint]).map(item => item.fingerprint))]
}
type ImportState = { scan: OnboardingImportScan | null; scanError: unknown; pickedItems: Record<string, boolean>; busy: boolean; report: OnboardingImportReport | null; failure: string }
const initial: ImportState = { scan: null, scanError: null, pickedItems: {}, busy: false, report: null, failure: '' }
export function useSetupImport() {
  const [state, change] = useReducer((state: ImportState, patch: Partial<ImportState>) => ({ ...state, ...patch }), initial)
  const generation = useRef(0)
  const writing = useRef(false)
  const load = useCallback(() => {
    const version = ++generation.current
    change({ scan: null, scanError: null })
    void api.onboardingImportScan().then(scan => {
      if (version !== generation.current) return
      const items = scan.sources.flatMap(source => source.items)
      change({ scan, pickedItems: Object.fromEntries(items.map(item => [item.fingerprint, writableImportItem(item) && item.preselected !== false])) })
    }).catch(scanError => { if (version === generation.current) change({ scanError }) })
  }, [])
  useEffect(() => { load(); return () => { generation.current += 1 } }, [load])
  const projection = useMemo(() => {
    const detected = state.scan?.sources.filter(source => source.detected) ?? []
    const fingerprints = selectedImportFingerprints(detected.flatMap(source => source.items), state.pickedItems)
    return { detected, fingerprints, nothingPicked: !fingerprints.length }
  }, [state.scan, state.pickedItems])
  const run = async () => {
    if (writing.current || projection.nothingPicked) return
    const version = generation.current
    writing.current = true; change({ busy: true, failure: '' })
    try {
      const report = await api.runOnboardingImport({ fingerprints: projection.fingerprints })
      if (version === generation.current) change({ report })
    } catch (failure) { if (version === generation.current) change({ failure: failure instanceof Error ? failure.message : 'The import could not be completed.' }) }
    finally { writing.current = false; if (version === generation.current) change({ busy: false }) }
  }
  return { ...state, ...projection, load, run,
    pickItems: (items: OnboardingImportItem[], value: boolean) => change({ pickedItems: { ...state.pickedItems, ...Object.fromEntries(items.filter(writableImportItem).map(item => [item.fingerprint, value])) } }),
  }
}
