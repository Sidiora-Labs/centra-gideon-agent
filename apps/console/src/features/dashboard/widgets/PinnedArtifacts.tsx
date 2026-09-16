import { useCallback, useEffect, useState } from 'react'
import { MoreRow } from '../../../shared/ui/MoreRow'
import { AnimatePresence } from 'framer-motion'
import { Package, PinOff } from 'lucide-react'
import { api, type Artifact, type PinnedArtifact } from '../../../shared/data/api'
import { SlotEmptyState, WidgetRow, RowAction } from './kit'
import type { RouteProps } from '../../../app/shell/useQueryState'

export function PinnedArtifacts({ navigate }: RouteProps) {
  const [pins, setPins] = useState<PinnedArtifact[] | null>(null)
  const [byslug, setBySlug] = useState<Record<string, Artifact> | null>({})
  const [pinsErr, setPinsErr] = useState<unknown>(null)

  const load = useCallback(async () => {
    try {
      const { pins: rows } = await api.pinnedArtifacts()
      let index: Record<string, Artifact> | null = null
      try {
        const all = await api.artifacts()
        index = {}
        for (const a of all) index[a.slug] = a
      } catch { index = null }
      setBySlug(index)
      setPins(rows)
      setPinsErr(null)
    } catch (e) {
      setPinsErr(e)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const unpin = async (slug: string) => {
    setPins((p) => (p ?? []).filter((x) => x.slug !== slug))
    try { await api.pinArtifact(slug, false) } catch { load() }
  }

  const resolved = byslug === null ? (pins ?? []) : (pins ?? []).filter((p) => !!byslug[p.slug])

  if (pinsErr) {
    return (
      <SlotEmptyState icon={Package}>
        Couldn't load your pins. They're safe — this is a read error.
      </SlotEmptyState>
    )
  }
  if (pins === null) return null
  if (resolved.length === 0) {
    return (
      <SlotEmptyState icon={Package}>
        No pinned artifacts. Pin one from its page to keep it here.
      </SlotEmptyState>
    )
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      <AnimatePresence initial={false}>
        {resolved.slice(0, 6).map((p) => {
          const art = byslug?.[p.slug]
          return (
            <WidgetRow
              key={p.slug}
              onClick={() => navigate(`artifacts?slug=${encodeURIComponent(p.slug)}`)}
              label={art?.name ?? p.slug}
              actions={
                <RowAction tone="default" onClick={() => unpin(p.slug)} title="Unpin"
                  ariaLabel={`Unpin: ${art?.name ?? p.slug}`}>
                  <PinOff size={15} />
                </RowAction>
              }
            >
              <span className="flex min-w-0 flex-col">
                {
}
                <span data-type="label-m" className="truncate text-on-surface">{art?.name ?? p.slug}</span>
                <span data-type="body-s" className="truncate text-on-surface-low">
                  {art ? art.kind : 'details unavailable'}
                  {art && art.version > 1 ? ` · v${art.version}` : ''}
                </span>
              </span>
            </WidgetRow>
          )
        })}
      </AnimatePresence>
      {
}
      <MoreRow total={resolved.length} shown={6} />
    </div>
  )
}
