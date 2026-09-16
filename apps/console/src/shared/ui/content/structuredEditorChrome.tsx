import { AlertTriangle, FileWarning, Save } from 'lucide-react'
import type { DocumentLossReport } from '../../data/api'
import { Button } from '../Button'
import { MoreRow } from '../MoreRow'
import { confirm } from '../dialog'

export function StructuredLossList({ loss }: { loss: DocumentLossReport }) {
  return <div data-type="body-s"><p className="text-on-surface">{loss.summary}</p>
    <ul className="mt-2 grid gap-1 text-on-surface-var">{loss.items.slice(0, 12).map((item, index) =>
      <li key={`${item.kind}:${item.where}:${index}`}><span className="text-on-surface">{item.kind}</span>{' · '}{item.where}{' — '}{item.detail}</li>,
    )}</ul><MoreRow total={loss.items.length} shown={12} noun="losses" />
  </div>
}
export function StructuredLossGate({ noun, loss, onAcknowledge }: { noun: 'workbook' | 'deck'; loss: DocumentLossReport; onAcknowledge: () => void }) {
  return <aside role="alert" className="border-b border-warning/30 bg-warning/5 p-l"><div className="flex gap-3">
    <AlertTriangle size={18} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
    <div className="min-w-0 space-y-2"><p data-type="body-m" className="font-medium text-on-surface">Editing this {noun} loses formatting</p>
      <p data-type="body-s" className="text-on-surface-var">It contains things this editor’s {noun === 'workbook' ? 'sheet' : 'deck'} model cannot hold. Saving re-creates the file, so they will not be in the saved copy. The version you have now is kept and can be restored from Details › Versions.</p>
      <StructuredLossList loss={loss} /><Button size="xs" variant="tonal" onClick={onAcknowledge}>I understand — edit anyway</Button>
    </div></div></aside>
}
export function confirmStructuredSave(title: string, noun: 'workbook' | 'deck', baseline: { loss: DocumentLossReport; version: number }) {
  return confirm({ title: `Save and re-render “${title}”?`, danger: true, confirmLabel: 'Save and re-render', icon: FileWarning,
    body: <div className="grid gap-3"><p data-type="body-s" className="text-on-surface">Saving re-creates the {noun} from the {noun === 'workbook' ? 'cells' : 'slides'} below, so the things Gideon cannot represent will not be in the saved copy:</p>
      <StructuredLossList loss={baseline.loss} /><p data-type="body-s" className="text-on-surface-var">Version {baseline.version} is kept — you can restore it from Details › Versions at any time.</p></div>,
  })
}
export function StructuredSaveControl({ dirty, editable, saving, reason, onSave }: { dirty: boolean; editable: boolean; saving: boolean; reason: string; onSave: () => void }) {
  return <div className="ml-auto flex items-center gap-2">{dirty && <span data-type="caption" className="text-on-surface-low">Unsaved changes</span>}
    <Button size="xs" variant="primary" shape="squircle" loading={saving} disabled={!editable || !dirty} disabledReason={reason || 'No changes to save yet.'} onClick={onSave}><Save size={14} aria-hidden="true" /> Save</Button>
  </div>
}
