import { useState } from 'react'
import { MoreRow } from '../../../shared/ui/MoreRow'
import { HardDrive } from 'lucide-react'
import { api } from '../../../shared/data/api'
import { useQuery, invalidateKeys } from '../../../shared/data/data'
import {
  occupantDetail, pressureDetail, pressureTone, sortOccupants,
} from '../../../shared/data/residency'
import { Meter } from '../../../shared/ui/Meter'
import { notify } from '../../../app/shell/appSdk'
import { confirm } from '../../../shared/ui/dialog'
import { RowAction, SlotEmptyState, WidgetRow } from './kit'

export function OnThisMachine() {
  const { data, error, refresh } = useQuery('models:loaded', () =>
    api.modelsLoaded(), { persist: false },
  )
  const [busy, setBusy] = useState('')

  if (!data && error) {
    return (
      <SlotEmptyState icon={HardDrive}>
        Couldn&rsquo;t read what&rsquo;s loaded on this machine.
      </SlotEmptyState>
    )
  }
  if (!data) return null

  const rows = sortOccupants(data.loaded)

  const unload = async (provider: string, subject: string) => {
    const ok = await confirm({
      title: `Unload ${provider}?`,
      body: 'Frees the memory this provider holds. The next request loads the model again.',
      confirmLabel: 'Unload',
    })
    if (!ok) return
    setBusy(provider)
    try {
      await api.unloadModelProvider(provider)
      invalidateKeys('models:loaded')
      refresh()
    } catch (e) {
      notify(`Couldn't unload ${subject}: ${String((e as Error)?.message || e)}`, 'error')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-s pt-xs">
      <Meter
        label="System memory in use"
        pct={data.pressure.used_pct}
        tone={pressureTone(data.pressure)}
        detail={pressureDetail(data.pressure)}
      />
      {rows.length === 0 ? (
        <SlotEmptyState icon={HardDrive}>
          No models are loaded. One loads on its first use.
        </SlotEmptyState>
      ) : (
        rows.slice(0, 5).map((row) => {
          const subject = row.model || row.provider
          return (
            <WidgetRow
              key={`${row.provider}:${row.model}`}
              actions={
                <RowAction
                  tone="default"
                  onClick={() => unload(row.provider, subject)}
                  title={busy === row.provider ? 'Unloading…' : 'Unload'}
                  ariaLabel={`Unload: ${subject}`}
                >
                  Unload
                </RowAction>
              }
            >
              <span className="flex min-w-0 flex-col">
                <span data-type="label-m" className="truncate text-on-surface">{subject}</span>
                <span data-type="body-s" className="truncate text-on-surface-low">
                  {row.provider} · {occupantDetail(row)}
                </span>
              </span>
            </WidgetRow>
          )
        })
      )}
      {
}
      <MoreRow total={rows.length} shown={5} />
    </div>
  )
}
