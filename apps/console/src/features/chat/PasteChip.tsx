import { useState } from 'react'
import { Clipboard, FileText } from 'lucide-react'
import { Modal } from '../../shared/ui/Modal'
import { PASTE_MARKER_RE } from './pasteBlocks'
import { splitFileRefs } from './parseAssistant'

export interface TurnPaste { seq: number; lines: number; content: string }

function PlainUserText({ text, onFileClick }: { text: string; onFileClick?: (path: string) => void }) {
  if (!onFileClick) return <span className="whitespace-pre-wrap break-words">{text}</span>
  return (
    <span className="whitespace-pre-wrap break-words">
      {splitFileRefs(text).map((part, i) =>
        part.kind === 'file' ? (
          <button key={i} type="button" onClick={() => onFileClick(part.value)} title={`Open ${part.value}`}
            className="mx-0.5 inline-flex items-center gap-1 rounded bg-surface-high px-1 align-baseline text-[0.92em] text-primary-emphasis transition-colors hover:bg-surface-highest">
            <FileText size={11} className="shrink-0" />{part.value.split('/').pop()}
          </button>
        ) : (
          <span key={i}>{part.value}</span>
        ),
      )}
    </span>
  )
}

export function PasteChip({ paste }: { paste: TurnPaste }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} title={`View paste #${paste.seq} (${paste.lines} lines)`}
        className="mx-0.5 inline-flex items-center gap-1 rounded-md bg-surface-high px-1.5 py-0.5 align-baseline text-[0.85em] text-primary-emphasis transition-colors hover:bg-surface-highest">
        <Clipboard size={11} className="shrink-0" /> Paste #{paste.seq}
        <span className="text-on-surface-low">· {paste.lines}L</span>
      </button>
      {open && (
        <Modal title={`Paste #${paste.seq} · ${paste.lines} lines`} icon={<Clipboard size={18} className="text-primary" />} onClose={() => setOpen(false)}>
          <pre data-type="body-s" className="overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-m py-s font-mono text-on-surface-var leading-relaxed">{paste.content}</pre>
        </Modal>
      )}
    </>
  )
}

export function MessageBody({ text, pastes, onFileClick }: { text: string; pastes?: TurnPaste[]; onFileClick?: (path: string) => void }) {
  if (!pastes || pastes.length === 0) return <PlainUserText text={text} onFileClick={onFileClick} />
  const bySeq = new Map(pastes.map((p) => [p.seq, p]))
  const parts: React.ReactNode[] = []
  let last = 0, m: RegExpExecArray | null, k = 0
  PASTE_MARKER_RE.lastIndex = 0
  while ((m = PASTE_MARKER_RE.exec(text)) !== null) {
    const seq = Number(m[1])
    const paste = bySeq.get(seq)
    if (!paste) continue
    if (m.index > last) parts.push(<PlainUserText key={`t${k}`} text={text.slice(last, m.index)} onFileClick={onFileClick} />)
    parts.push(<PasteChip key={`p${k}`} paste={paste} />)
    last = m.index + m[0].length
    k++
  }
  if (last < text.length) parts.push(<PlainUserText key={`t${k}`} text={text.slice(last)} onFileClick={onFileClick} />)
  return <div>{parts}</div>
}
