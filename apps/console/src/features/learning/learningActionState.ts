import { useEffect, useReducer, useRef } from 'react'
import { api, type IdentityReportView, type RetrievalLabelCard } from '../../shared/data/api'

type IdentityState = { cadence: IdentityReportView['cadence']; busy: boolean; failure: string; slug: string }
type IdentityPatch = Partial<IdentityState>
const identityPatch = (state: IdentityState, patch: IdentityPatch): IdentityState => ({ ...state, ...patch })

export function useIdentityReportActions(report: IdentityReportView | undefined, onRetry: () => void, onDelivered: () => void) {
  const [state, update] = useReducer(identityPatch, { cadence: report?.cadence ?? '', busy: false, failure: '', slug: '' })
  const lifetime = useRef(true)
  const writing = useRef(false)
  const revision = useRef(0)
  const pending = useRef(0)
  const committed = useRef(report?.cadence ?? '')
  const queue = useRef<Promise<void>>(Promise.resolve())
  useEffect(() => { lifetime.current = true; return () => { lifetime.current = false } }, [])
  useEffect(() => {
    if (report?.cadence === undefined || pending.current > 0) return
    committed.current = report.cadence
    update({ cadence: report.cadence })
  }, [report?.cadence])

  function setCadenceTo(next: string) {
    if (next !== 'monthly' && next !== 'weekly' && next !== 'off') return
    const ticket = ++revision.current
    pending.current += 1
    update({ cadence: next, failure: '' })
    queue.current = queue.current.then(async () => {
      try {
        await api.patchConfig('learning.identity_report_cadence', next)
        committed.current = next
        if (lifetime.current) onRetry()
      } catch (error) {
        if (lifetime.current && revision.current === ticket) {
          update({ cadence: committed.current, failure: `Couldn't save “Write one automatically”: ${String((error as Error)?.message || error)}` })
        }
      } finally {
        pending.current -= 1
      }
    })
  }

  async function write() {
    if (!report || report.total === 0 || writing.current) return
    writing.current = true
    update({ busy: true, failure: '' })
    try {
      const delivery = await api.deliverIdentityReport(report.window_days)
      if (lifetime.current) {
        update({ slug: delivery.artifact_slug })
        onDelivered()
      }
    } catch (error) {
      if (lifetime.current) update({ failure: error instanceof Error ? error.message : 'could not write the report' })
    } finally {
      writing.current = false
      if (lifetime.current) update({ busy: false })
    }
  }
  return { ...state, setCadenceTo, write }
}

type LabelState = {
  store: string; card: RetrievalLabelCard | null; picked: Record<string, string[]>
  busy: boolean; err: string; saved: string
}
type LabelAction = { type: 'patch'; value: Partial<LabelState> } | { type: 'toggle'; query: string; id: string }
const emptyLabels: LabelState = { store: '', card: null, picked: {}, busy: false, err: '', saved: '' }
function labelState(state: LabelState, action: LabelAction): LabelState {
  if (action.type === 'patch') return { ...state, ...action.value }
  const values = new Set(state.picked[action.query] ?? [])
  if (!values.delete(action.id)) values.add(action.id)
  return { ...state, picked: { ...state.picked, [action.query]: Array.from(values) } }
}

export function useRetrievalLabelDraft() {
  const [state, dispatch] = useReducer(labelState, emptyLabels)
  const epoch = useRef(0)
  const active = useRef(false)
  const patch = (value: Partial<LabelState>) => dispatch({ type: 'patch', value })
  useEffect(() => () => { epoch.current += 1 }, [])

  async function open(store: string) {
    if (active.current) return
    active.current = true
    const request = ++epoch.current
    patch({ ...emptyLabels, store, busy: true })
    try {
      const card = await api.retrievalLabelCard(store)
      const picked: Record<string, string[]> = Object.create(null)
      for (const entry of card.queries) picked[entry.query] = entry.already_relevant.slice()
      if (epoch.current === request) patch({ card, picked })
    } catch (error) {
      if (epoch.current === request) patch({ err: error instanceof Error ? error.message : String(error) })
    } finally {
      active.current = false
      if (epoch.current === request) patch({ busy: false })
    }
  }

  async function save() {
    const { card, picked } = state
    if (!card || active.current) return
    active.current = true
    const request = ++epoch.current
    patch({ busy: true, err: '', saved: '' })
    const answers: Record<string, string[]> = Object.create(null)
    for (const entry of card.queries) answers[entry.query] = [...(picked[entry.query] ?? [])]
    try {
      const result = await api.saveRetrievalLabels(card.store, answers)
      if (epoch.current === request) patch({ card: null, saved: `Saved ${result.hand_labelled} hand-labelled of ${result.queries} queries.` })
    } catch (error) {
      if (epoch.current === request) patch({ err: error instanceof Error ? error.message : String(error) })
    } finally {
      active.current = false
      if (epoch.current === request) patch({ busy: false })
    }
  }
  return {
    ...state, open, save,
    toggle: (query: string, id: string) => dispatch({ type: 'toggle', query, id }),
    clearError: () => patch({ err: '' }),
    cancel: () => { if (!active.current) patch({ card: null, store: '' }) },
  }
}
