import { Component, type ReactNode } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'
import { fvs } from '../../shared/theme/fontWeight'
import { treatmentPaint } from '../../shared/theme/errorTreatments'
import { readableErrText } from '../../shared/data/errText'
import { useErrorTreatment } from './personality'
import { canReloadChunk, isChunkLoadError, RELOAD_GUARD_KEY } from './errorRecovery'

interface Props { children: ReactNode; resetKey?: string }
interface State { error: unknown | null }
function ErrorFallback({ error, retry }: { error: unknown; retry: () => void }) {
  const treatment = useErrorTreatment(), chunk = isChunkLoadError(error)
  const message = chunk ? 'The app was updated while this tab was open. Reload to load the latest version.' : readableErrText(error) || 'Something went wrong rendering this view.'
  return <div className={['mx-auto flex h-full max-w-2xl flex-col items-center justify-center gap-l rounded-xl border border-outline-variant/40 px-2xl py-3xl text-center', treatment?.surfaceClass].filter(Boolean).join(' ')} style={treatmentPaint(treatment) ?? undefined}>
    <AlertTriangle size={32} className={treatment?.iconClass ?? 'text-on-surface-low'} />
    <h2 className="text-on-surface text-[1.0625rem]" style={fvs(500)}>{chunk ? 'A new version is available' : 'This page hit an error'}</h2>
    <p className="max-w-md text-on-surface-low text-[0.8125rem]">{message}</p>
    <button type="button" onClick={retry} className="inline-flex h-11 items-center gap-2 rounded-xl px-5 text-[0.8125rem]" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
      <RotateCcw size={14} /> {chunk ? 'Reload app' : 'Retry'}
    </button>
  </div>
}
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }
  static getDerivedStateFromError(error: unknown): State { return { error } }
  private retry = () => {
    if (isChunkLoadError(this.state.error)) window.location.reload()
    else this.setState({ error: null })
  }
  componentDidUpdate(previous: Props) {
    if (previous.resetKey !== this.props.resetKey && this.state.error !== null) this.setState({ error: null })
  }
  componentDidCatch(error: unknown) {
    if (!isChunkLoadError(error)) return
    let previous: string | null = null
    try { previous = sessionStorage.getItem(RELOAD_GUARD_KEY) } catch { /* Recovery also works without storage. */ }
    const now = Date.now()
    if (!canReloadChunk(now, previous)) return
    try { sessionStorage.setItem(RELOAD_GUARD_KEY, String(now)) } catch { /* Manual recovery remains available. */ }
    window.location.reload()
  }
  render() { return this.state.error === null ? this.props.children : <ErrorFallback error={this.state.error} retry={this.retry} /> }
}
