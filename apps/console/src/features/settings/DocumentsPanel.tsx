import { useEffect, useState } from 'react'
import { api, type DashboardConfig } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, Row, Toggle, SavedToast } from './settingsUI'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { setDocumentEditing } from '../../shared/ui/content/documentEditing'

export function DocumentsPanel() {
  const [cfg, setCfg] = useState<DashboardConfig | null>(null)
  const [saved, setSaved] = useState(false)

  const { data, error: loadErr, refresh } = useQuery('settings:documents', () => api.dashboardConfig())

  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} what="settings" />

  const save = (patch: Partial<DashboardConfig>) => {
    const prev = cfg
    setCfg({ ...cfg, ...patch })
    api.saveDashboardConfig(patch)
      .then(() => {
        setSaved(true)
        setTimeout(() => setSaved(false), 1500)
        if (patch.document_editing !== undefined) setDocumentEditing(!!patch.document_editing)
      })
      .catch((e) => {
        setCfg(prev)
        notify(`Couldn't save document settings: ${String((e as Error)?.message || e)}`, 'error')
      })
  }

  return (
    <div>
      <PanelHeader title="Documents" hint="How generated Word, Excel and PowerPoint files behave. Download-only by default — editing one re-creates it, which is a trade worth choosing deliberately." />

      <Section title="Editing" hint="Whether an office document opens in an editor or stays download-only.">
        <RowGroup>
          <Row label="Edit documents in place"
            hint="Opens a generated Word document in a structural editor instead of download-only. Saving RE-CREATES the file from the structure Gideon could parse, so constructs its document model cannot hold (comments, footnotes, embedded objects, exact styling) are not in the saved copy. The editor lists them before your first edit and repeats them in the save confirmation, and the version you started from is always restorable from the document's Details › Versions. With this off, office documents are read-only previews and the server refuses a document save outright.">
            <div className="flex items-center gap-2">
              <SavedToast show={saved} />
              <Toggle on={cfg.document_editing} onChange={(v) => save({ document_editing: v })} label="Edit documents in place" />
            </div>
          </Row>
        </RowGroup>
      </Section>
    </div>
  )
}
