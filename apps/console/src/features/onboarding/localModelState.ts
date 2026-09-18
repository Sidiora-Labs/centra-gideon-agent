import { useRef, useState } from 'react'
import { api, type LocalModelDetection, type LocalModelOffer, type LocalModelScanReport } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { setupErrorText } from './essentialSetupState'

export const LOCAL_MODEL_KEY = 'onboarding:local-models'

export function parseTargets(text: string): string[] {
  return text.split(/[\s,]+/).map(entry => entry.trim()).filter(Boolean)
}

type ScanState = { report: LocalModelScanReport | null; error: string; busy: boolean }
const NO_SCAN: ScanState = { report: null, error: '', busy: false }

export function useLocalModelOnboarding(onBound: (offer: LocalModelOffer, model: string) => void) {
  const { data: detection, error: detectError, refresh } = useQuery<LocalModelDetection>(LOCAL_MODEL_KEY, () => api.detectLocalModels())
  const [targets, setTargets] = useState('')
  const [scan, setScan] = useState<ScanState>(NO_SCAN)
  const [binding, setBinding] = useState('')
  const [bindError, setBindError] = useState('')
  const scanning = useRef(false)
  const bindingNow = useRef(false)

  const runScan = async () => {
    const list = parseTargets(targets)
    if (scanning.current || list.length === 0) return
    scanning.current = true
    setScan({ report: null, error: '', busy: true })
    try {
      setScan({ report: await api.scanLocalModels({ targets: list }), error: '', busy: false })
    } catch (failure) {
      setScan({ report: null, error: setupErrorText(failure) || 'The scan could not be completed.', busy: false })
    } finally { scanning.current = false }
  }

  const bind = async (offer: LocalModelOffer) => {
    if (bindingNow.current) return
    bindingNow.current = true
    setBinding(offer.endpoint); setBindError('')
    try {
      const result = await api.bindLocalModel({ endpoint: offer.endpoint })
      onBound(offer, result.model)
    } catch (failure) {
      setBinding('')
      setBindError(setupErrorText(failure) || 'That local model could not be bound.')
    } finally { bindingNow.current = false }
  }

  return {
    detection, detectError, refresh, targets, setTargets, binding, bindError, bind, runScan,
    scanReport: scan.report, scanError: scan.error, scanning: scan.busy,
    offers: scan.report?.offers.filter(offer => offer.ok) ?? [],
    canScan: parseTargets(targets).length > 0,
  }
}
