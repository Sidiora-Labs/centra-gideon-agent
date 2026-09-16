import { useCallback, useEffect, useRef, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { Palette } from 'lucide-react'
import { Segmented } from '../../shared/ui/Segmented'
import { api } from '../../shared/data/api'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { TokensView, ContrastView, type ResolvedTokens, type Scheme } from './DesignCockpitPage'

export function DesignStepPreview({ loopId, stepKind, overrides }: {
  loopId: string
  stepKind: string
  overrides: Record<string, unknown>
}) {
  const [scheme, setScheme] = useState<Scheme>('light')
  const [tokens, setTokens] = useState<ResolvedTokens | null>(null)
  const [tab, setTab] = useState<'tokens' | 'contrast'>('tokens')
  const [err, setErr] = useState(false)
  const merged = useRef(false)

  const loadTokens = useCallback(async () => {
    try { const t = await api.uLoopDesignTokens(loopId, scheme); setTokens(t as ResolvedTokens); setErr(false) }
    catch { setErr(true) }
  }, [loopId, scheme])

  useEffect(() => {
    let alive = true
    const run = async () => {
      if (!merged.current && overrides && Object.keys(overrides).length) {
        merged.current = true
        try {
          const loop = await api.uLoop(loopId)
          const kc = (loop.kind_config || {}) as Record<string, unknown>
          const next = deepMerge((kc.token_overrides as Record<string, unknown>) || {}, overrides)
          await api.updateULoop(loopId, { kind_config: { ...kc, token_overrides: next } })
        } catch {   }
      }
      if (alive) await loadTokens()
    }
    run()
    return () => { alive = false }
  }, [loopId, overrides, loadTokens])

  const setOverride = useCallback(async (path: string, value: string) => {
    const ok = await reportingWrite(`save the ${path} override`, async () => {
      const loop = await api.uLoop(loopId)
      const kc = (loop.kind_config || {}) as Record<string, unknown>
      const ov = JSON.parse(JSON.stringify(kc.token_overrides || {}))
      const segs = path.split('.')
      const chain: Record<string, unknown>[] = [ov]
      let node: Record<string, unknown> = ov
      for (let i = 0; i < segs.length - 1; i++) { node[segs[i]] = (node[segs[i]] as Record<string, unknown>) || {}; node = node[segs[i]] as Record<string, unknown>; chain.push(node) }
      const leaf = segs[segs.length - 1]
      if (value.trim()) node[leaf] = value.trim()
      else {
        delete node[leaf]
        for (let i = chain.length - 1; i > 0; i--) { if (Object.keys(chain[i]).length === 0) delete chain[i - 1][segs[i - 1]]; else break }
      }
      await api.updateULoop(loopId, { kind_config: { ...kc, token_overrides: ov } })
    })
    if (ok) await loadTokens()
  }, [loopId, loadTokens])

  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-low/40 p-2.5">
      <div className="mb-2 flex items-center gap-2">
        <Palette size={13} className="text-primary" />
        <span data-type="caption" className="text-on-surface" style={fvs(600)}>
          Live design system — net effect of every choice so far
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Segmented ariaLabel="View" value={tab} onChange={(v) => setTab(v as 'tokens' | 'contrast')}
            options={[{ key: 'tokens', label: 'Tokens' }, { key: 'contrast', label: 'Contrast' }]} />
          <Segmented ariaLabel="Scheme" value={scheme} onChange={(v) => setScheme(v as Scheme)}
            options={[{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }]} />
        </div>
      </div>
      <p data-type="caption" className="mb-2 text-on-surface-low">
        {stepKind === 'palette' ? 'The extracted palette is applied below.' : stepKind === 'typography' ? 'The extracted type + spacing are applied below.' : 'The extracted foundation tokens are applied below.'} Click a swatch / value to edit it — changes apply to the whole system instantly and carry into the cockpit.
      </p>
      {err
        ? <div data-type="caption" className="text-on-surface-low">Couldn't load the live preview.</div>
        : tab === 'tokens'
          ? <TokensView tokens={tokens} scheme={scheme} onOverride={setOverride} />
          : <ContrastView tokens={tokens} scheme={scheme} />}
    </div>
  )
}

function deepMerge(base: Record<string, unknown>, patch: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = JSON.parse(JSON.stringify(base || {}))
  for (const [k, v] of Object.entries(patch || {})) {
    if (v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) {
      out[k] = deepMerge(out[k] as Record<string, unknown>, v as Record<string, unknown>)
    } else {
      out[k] = v
    }
  }
  return out
}
