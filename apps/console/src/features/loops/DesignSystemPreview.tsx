import { useEffect, useState } from 'react'
import { Palette, Contrast } from 'lucide-react'
import { Modal } from '../../shared/ui/Modal'
import { Segmented } from '../../shared/ui/Segmented'
import { api } from '../../shared/data/api'
import { TokensView, ContrastView, type ResolvedTokens, type Scheme } from './DesignCockpitPage'

export function DesignSystemPreview({ onClose }: { onClose: () => void }) {
  const [scheme, setScheme] = useState<Scheme>('light')
  const [tokens, setTokens] = useState<ResolvedTokens | null>(null)
  const [tab, setTab] = useState<'tokens' | 'contrast'>('tokens')
  const [err, setErr] = useState(false)

  useEffect(() => {
    let alive = true
    api.designDefaultTokens(scheme)
      .then((t) => { if (alive) { setTokens(t as ResolvedTokens); setErr(false) } })
      .catch(() => { if (alive) setErr(true) })
    return () => { alive = false }
  }, [scheme])

  return (
    <Modal title="Gideon design system" icon={<Palette size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-l p-l">
        <p data-type="body-s" className="text-on-surface-low max-w-[52rem]">
          The canonical default tokens every Design loop starts from — variables for every look-and-feel
          axis. Launch a Design loop to override any of these and generate React components from the system.
        </p>
        <div className="flex items-center gap-2">
          <Segmented ariaLabel="View" value={tab} onChange={(v) => setTab(v as 'tokens' | 'contrast')}
            options={[{ key: 'tokens', label: 'Tokens' }, { key: 'contrast', label: 'Contrast' }]} />
          <div className="ml-auto">
            <Segmented ariaLabel="Scheme" value={scheme} onChange={(v) => setScheme(v as Scheme)}
              options={[{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }]} />
          </div>
        </div>
        {err
          ? <div data-type="body-s" className="text-on-surface-low">Could not load the default token set.</div>
          : tab === 'tokens'
            ? <TokensView tokens={tokens} scheme={scheme} readOnly />
            : <div className="flex flex-col gap-2"><div data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low uppercase tracking-wide"><Contrast size={12} /> WCAG contrast</div><ContrastView tokens={tokens} scheme={scheme} /></div>}
      </div>
    </Modal>
  )
}
