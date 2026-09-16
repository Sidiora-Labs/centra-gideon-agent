import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { layerName, type SurfaceLayer } from './layers'

interface Props { layer: SurfaceLayer; what: string; children: ReactNode; fallback?: ReactNode }
interface State { identity: string; failed: boolean }
const identityOf = (props: Props) => JSON.stringify([props.layer, props.what])

export class LayerBoundary extends Component<Props, State> {
  state: State = { identity: identityOf(this.props), failed: false }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    const identity = identityOf(props)
    return identity === state.identity ? null : { identity, failed: false }
  }

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[surfaces] ${layerName(this.props.layer)}-layer surface "${this.props.what}" failed to render`, error, info.componentStack)
  }

  render(): ReactNode {
    const { what, layer, fallback, children } = this.props
    if (!this.state.failed) return children
    if (fallback !== undefined) return fallback
    return <div role="alert" data-type="caption" className="flex items-start gap-2 rounded-lg border border-warn/25 bg-warn/5 px-3 py-2 text-warn">
      <AlertTriangle size={13} aria-hidden className="mt-0.5 shrink-0" />
      <span className="min-w-0 break-words">{what} ({layerName(layer)} layer) could not render — the rest of this surface is unaffected.</span>
    </div>
  }
}
