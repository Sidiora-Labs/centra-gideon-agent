import { useInboxOperation } from './inboxQueueState'
import { useState } from 'react'
import { StickyNote } from 'lucide-react'
import { Modal } from '../../shared/ui/Modal'
import { Button } from '../../shared/ui/Button'
import { Field, FieldError, TextArea } from '../../shared/ui/forms'
import { notify } from '../../app/shell/appSdk'
import { api, type InboxItem } from '../../shared/data/api'

const MAX_CHARS = 4000

const COUNTER_FROM = Math.floor(MAX_CHARS * 0.9)

export function ComposeNoteModal({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (item: InboxItem) => void
}) {
  const [text, setText] = useState('')
  const operation = useInboxOperation('capture-note')
  const { err, setErr } = operation
  const saving = operation.busy !== null

  const trimmed = text.trim()
  const tooLong = trimmed.length > MAX_CHARS

  const save = () => {
    const problem = !trimmed ? 'Type what you want to remember, then save.' : tooLong ? `That note is ${trimmed.length} characters; the limit is ${MAX_CHARS}. Shorten it and save again.` : ''
    if (problem) { setErr(problem); return }
    void operation.run('save', () => api.createInboxNote(trimmed), response => { notify('Note saved to your inbox.', 'success'); onCreated(response.item) }, "Couldn't save this note.")
  }

  return (
    <Modal title="Capture a note" icon={<StickyNote size={18} className="text-primary" />} onClose={onClose}>
      <form className="grid gap-m" onSubmit={event => { event.preventDefault(); save() }}>
        <Field
          label="Note"
          hint="Its first line becomes the subject in your inbox. Everything else is the body."
          right={trimmed.length >= COUNTER_FROM
            ? <span data-type="caption" className="tabular-nums" style={{ color: tooLong ? 'var(--color-danger)' : 'var(--color-on-surface-low)' }}>{trimmed.length}/{MAX_CHARS}</span>
            : undefined}>
          <TextArea value={text} onChange={(v) => { setText(v); setErr('') }} rows={7} autoFocus
            placeholder="Ask about the invoice discrepancy before Friday" />
        </Field>
        {err && <FieldError>{err}</FieldError>}
        <div className="flex items-center justify-end gap-s border-t border-outline/30 pt-m">
          <Button type="submit" size="sm" loading={saving} disabled={saving || !trimmed || tooLong}
            disabledReason={saving ? undefined : !trimmed ? 'Write the note first' : 'Shorten the note to save it'}>
            {saving ? 'Saving…' : 'Save to inbox'}
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Modal>
  )
}
