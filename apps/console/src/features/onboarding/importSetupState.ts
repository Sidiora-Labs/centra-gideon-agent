import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import { api, type OnboardingImportReport, type OnboardingImportScan } from '../../shared/data/api'

type ImportState = { scan: OnboardingImportScan | null; scanError: unknown; pickedSources: Record<string, boolean>; pickedCategories: Record<string, boolean>; busy: boolean; report: OnboardingImportReport | null; failure: string }
const initial: ImportState = { scan: null, scanError: null, pickedSources: {}, pickedCategories: {}, busy: false, report: null, failure: '' }
export function useSetupImport() {
  const [state, change] = useReducer((state: ImportState, patch: Partial<ImportState>) => ({ ...state, ...patch }), initial)
  const generation = useRef(0)
  const writing = useRef(false)
  const load = useCallback(() => {
    const version = ++generation.current
    change({ scan: null, scanError: null })
    void api.onboardingImportScan().then(scan => {
      if (version !== generation.current) return
      change({ scan, pickedSources: Object.fromEntries(scan.sources.filter(source => source.detected).map(source => [source.source, true])), pickedCategories: Object.fromEntries(scan.categories.map(category => [category, true])) })
    }).catch(scanError => { if (version === generation.current) change({ scanError }) })
  }, [])
  useEffect(() => { load(); return () => { generation.current += 1 } }, [load])
  const projection = useMemo(() => {
    const detected = state.scan?.sources.filter(source => source.detected) ?? []
    const chosenSources = detected.filter(source => state.pickedSources[source.source])
    const counts = new Map<string, { total: number; existing: number }>()
    chosenSources.forEach(source => source.items.forEach(item => {
      const count = counts.get(item.category) ?? { total: 0, existing: 0 }
      counts.set(item.category, { total: count.total + 1, existing: count.existing + Number(item.existing) })
    }))
    const offered = (state.scan?.categories ?? []).filter(category => counts.has(category))
    const chosenCategories = offered.filter(category => state.pickedCategories[category])
    return { detected, chosenSources, chosenCategories, offered, tally: (category: string) => counts.get(category) ?? { total: 0, existing: 0 }, nothingPicked: !chosenSources.length || !chosenCategories.length }
  }, [state.scan, state.pickedSources, state.pickedCategories])
  const run = async () => {
    if (writing.current || projection.nothingPicked) return
    const version = generation.current
    writing.current = true; change({ busy: true, failure: '' })
    try {
      const report = await api.runOnboardingImport({ sources: projection.chosenSources.map(source => source.source), categories: projection.chosenCategories })
      if (version === generation.current) change({ report })
    } catch (failure) { if (version === generation.current) change({ failure: (failure as Error)?.message || 'The import could not be completed.' }) }
    finally { writing.current = false; if (version === generation.current) change({ busy: false }) }
  }
  return { ...state, ...projection, load, run,
    pickSource: (key: string, value: boolean) => change({ pickedSources: { ...state.pickedSources, [key]: value } }),
    pickCategory: (key: string, value: boolean) => change({ pickedCategories: { ...state.pickedCategories, [key]: value } }),
  }
}
