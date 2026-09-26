import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { Maximize2, Minimize2, Box } from 'lucide-react'
import { IconButton } from '../../shared/ui/IconButton'
import { useFocusTrap } from '../../shared/ui/useFocusTrap'
import { useResizablePanel } from '../../shared/ui/useResizablePanel'
import { Modal } from '../../shared/ui/Modal'
import { Button } from '../../shared/ui/Button'
import { TextInput } from '../../shared/ui/forms'
import { spring } from '../../shared/theme/motion'
import { api, type FsEntry } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { FileViewer, type FileViewerHandle } from '../files/browse/FileViewer'
import { baseName } from '../files/fileMeta'
import type { CommentTarget } from '../../shared/ui/content/commentTarget'
import { CanvasSplit, CanvasSplitThread, CanvasSplitMessage, CanvasSplitDocument, CanvasSplitHeader, CanvasSplitBody } from '../../shared/vendor/assistant-ui/elements/canvas-split'
import type { ChatTurn } from './chatTypes'
import { recentCanvasTurns } from './auiThreadSurfaces'

const MIN_W = 360, MAX_W = 900, DEFAULT_W = 480

function ExpandedOverlay({ children }: { children: ReactNode }) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  return (
    <motion.div ref={trapRef} className="fixed inset-0 z-[var(--z-content)] flex flex-col bg-surface"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
      {children}
    </motion.div>
  )
}

export function ChatFilePanel({ path, onClose, commentTarget, contextTurns = [] }: { path: string; onClose: () => void; commentTarget?: CommentTarget; contextTurns?: readonly ChatTurn[] }) {
  const { width, fitWidth: dockW, onHandleDown, onHandleKey, min, max } = useResizablePanel(
    'chat-file', { def: DEFAULT_W, min: MIN_W, max: MAX_W, side: 'right' })
  const [expanded, setExpanded] = useState(false)
  const [artModal, setArtModal] = useState<{ entry: FsEntry; content: string; name: string } | null>(null)
  const viewerRef = useRef<FileViewerHandle>(null)
  const entry: FsEntry = { name: baseName(path), path, is_dir: false }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); viewerRef.current?.save() }
      else if (e.key === 'Escape') { if (expanded) setExpanded(false); else onClose() }
    }
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey)
  }, [expanded, onClose])

  const saveAsArtifact = (e: FsEntry, content: string) => setArtModal({ entry: e, content, name: baseName(e.path) })
  const confirmArtifact = async () => {
    if (!artModal || !artModal.name.trim()) return
    try {
      await api.createArtifact({ name: artModal.name.trim(), content: artModal.content, source: 'manual', source_path: artModal.entry.path, kind: guessKind(artModal.entry.name) })
      setArtModal(null)
    } catch (e) { notify(`Could not save artifact: ${(e as Error).message}`, 'error') }
  }

  const dir = path.replace(/\/+$/, '').replace(/\/[^/]*$/, '')
  const chrome = <CanvasSplitHeader title={dir ? `${dir}/${baseName(path)}` : baseName(path)} onClose={onClose}
    actions={<IconButton icon={expanded ? Minimize2 : Maximize2} label={expanded ? 'Collapse to panel' : 'Expand to full width'} size={28} onClick={() => setExpanded((v) => !v)} />}/>

  const body = (
    <div className="flex h-full flex-col">
      <CanvasSplit className="h-full max-w-none rounded-none border-0 md:h-full">
        {expanded && contextTurns.length > 0 && <CanvasSplitThread aria-label="Recent conversation" className="md:w-64">
          {recentCanvasTurns(contextTurns).map((turn, index) => <CanvasSplitMessage key={index} speaker={turn.speaker}>
            {turn.text}
          </CanvasSplitMessage>)}
        </CanvasSplitThread>}
        <CanvasSplitDocument className="min-h-0">
          {chrome}
          <CanvasSplitBody className="min-h-0 overflow-hidden p-0">
            <FileViewer ref={viewerRef} entry={entry} compact={!expanded} onSaved={() => {}} onSaveAsArtifact={saveAsArtifact} commentTarget={commentTarget} />
          </CanvasSplitBody>
        </CanvasSplitDocument>
      </CanvasSplit>
      {artModal && (
        <Modal title="Save as artifact" icon={<Box size={18} className="text-primary" />} onClose={() => setArtModal(null)}>
          <div className="flex flex-col gap-m p-l" style={{ minWidth: 360 }}>
            <p data-type="body-s" className="text-on-surface-low">Creates a versioned artifact that live-points at <span className="font-mono">{baseName(artModal.entry.path)}</span>. Re-saving bumps it instead of duplicating.</p>
            <TextInput value={artModal.name} onChange={(v) => setArtModal((m) => m && { ...m, name: v })} placeholder="Artifact name" autoFocus />
            <div className="flex justify-end gap-s">
              <Button variant="ghost" size="sm" onClick={() => setArtModal(null)}>Cancel</Button>
              <Button size="sm" onClick={confirmArtifact}>Save artifact</Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )

  if (expanded) {
    return createPortal(<ExpandedOverlay>{body}</ExpandedOverlay>, document.body)
  }

  return (
    <motion.div className="relative shrink-0 overflow-hidden border-l border-outline-variant/40 bg-surface"
      initial={{ width: 0, opacity: 0 }} animate={{ width: dockW, opacity: 1 }} exit={{ width: 0, opacity: 0 }} transition={spring.spatialDefault}>
      {
}
      <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="vertical"
        tabIndex={0} aria-label="Resize file panel — arrow keys to resize"
        aria-valuenow={Math.round(width)} aria-valuemin={min} aria-valuemax={max}
        className="absolute left-0 top-0 bottom-0 z-20 w-1.5 cursor-ew-resize outline-none group">
        <span className="absolute left-0 top-0 bottom-0 w-px bg-outline-variant/40 group-hover:bg-primary group-focus-visible:bg-primary transition-colors" />
      </div>
      <div className="h-full" style={{ width: dockW }}>{body}</div>
    </motion.div>
  )
}

function guessKind(name: string): string {
  const ext = name.toLowerCase().split('.').pop() || ''
  if (ext === 'html' || ext === 'htm') return 'html'
  if (ext === 'svg') return 'svg'
  if (ext === 'json') return 'json'
  if (['md', 'markdown', 'mdx', 'txt'].includes(ext)) return 'markdown'
  return 'text'
}
