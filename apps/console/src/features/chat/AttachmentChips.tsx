import { ComposerAttachmentChip, ComposerAttachments } from '../../shared/vendor/assistant-ui/elements/composer'

export type AttachmentDelivery = 'pixels' | 'text' | 'unread' | 'pending'

export interface ChatAttachment {
  id: string
  name: string
  meta?: string
  kind?: 'image' | 'text' | 'archive'
  state?: 'uploading' | 'done' | 'error'
  progress?: number
  delivery?: AttachmentDelivery
  reason?: string
}

export function attachmentDeliveryLabel(attachment: ChatAttachment): string {
  if (attachment.delivery === 'pixels') return 'Image will be sent as pixels'
  if (attachment.delivery === 'text') return attachment.reason || 'Image will be read as text'
  if (attachment.delivery === 'unread') return attachment.reason || 'Image was not read'
  if (attachment.delivery === 'pending') return attachment.reason || 'Image delivery will be checked before sending'
  return attachment.meta || 'Attached'
}

export function AttachmentChips({ attachments, onRemove, onOpen }: {
  attachments: readonly ChatAttachment[]
  onRemove?: (id: string) => void
  onOpen?: (id: string) => void
}) {
  if (!attachments.length) return null
  return <ComposerAttachments aria-label="Attached files">
    {attachments.map(attachment => {
      const image = attachment.kind === 'image'
      const delivery = attachmentDeliveryLabel(attachment)
      const meta = image ? delivery : attachment.meta || 'Attached'
      return <div key={attachment.id} className="flex items-center gap-1" data-image-delivery={image ? attachment.delivery ?? 'pending' : undefined}>
        <span className="sr-only">{image ? delivery : ''}</span>
        <ComposerAttachmentChip
          attachment={{ name: attachment.name, meta, state: attachment.state ?? 'done', kind: attachment.kind, progress: attachment.progress }}
          onRemove={onRemove ? () => onRemove(attachment.id) : undefined}
        />
        {onOpen && (attachment.state ?? 'done') === 'done' && <button type="button" aria-label={`Open ${attachment.name}`}
          onClick={() => onOpen(attachment.id)} className="rounded-lg px-2 py-1 text-xs text-primary hover:bg-primary/10">Open</button>}
      </div>
    })}
  </ComposerAttachments>
}
