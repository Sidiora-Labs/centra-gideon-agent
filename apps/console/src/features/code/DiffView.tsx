import { lazy, Suspense, useEffect, useState } from 'react'
import { Loader2, RotateCcw } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { api } from '../../shared/data/api'
import { useMode } from '../../app/shell/theme'
import { monacoLang } from '../files/fileMeta'

import { useDiffTeardown } from '../../shared/ui/useDiffTeardown'
const MonacoDiff = lazy(() => import('@monaco-editor/react').then((m) => ({ default: m.DiffEditor })))

export function DiffView({ path, name, ws, deleted = false }: { path: string; name: string; ws: string; deleted?: boolean }) {
  const { mode } = useMode()
  const onDiffMount = useDiffTeardown()
  const [original, setOriginal] = useState<string | null>(null)
  const [modified, setModified] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    setOriginal(null); setModified(null); setError(null); setTruncated(false)
    const workRead = deleted
      ? api.fileRead(path, true).then((r) => ({ content: r.content, truncated: r.truncated })).catch(() => ({ content: '', truncated: false }))
      : api.fileRead(path, true).then((r) => ({ content: r.content, truncated: r.truncated }))
    Promise.all([
      api.fileGitOriginal(path).then((r) => ({ content: r.content, truncated: !!r.truncated })).catch(() => ({ content: '', truncated: false })),
      workRead,
    ]).then(([orig, work]) => {
      if (!alive) return
      setOriginal(orig.content); setModified(work.content); setTruncated(orig.truncated || work.truncated)
    }).catch(() => { if (alive) setError('Could not load the diff.') })
    return () => { alive = false }
  }, [path, attempt, deleted])

  if (error) return (
    <div data-type="body-s" className="flex h-full flex-col items-center justify-center gap-2 text-on-surface-low">
      <span>{error}</span>
      <Button variant="secondary" size="sm" onClick={() => setAttempt((n) => n + 1)}><RotateCcw size={15} /> Try again</Button>
    </div>
  )
  if (original === null || modified === null) {
    return <div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>
  }
  const rel = (() => {
    const base = ws.replace(/\/$/, '').split('/').pop() || ''
    const marker = `/${base}/`; const i = base ? path.lastIndexOf(marker) : -1
    return i >= 0 ? path.slice(i + marker.length) : name
  })()
  return (
    <div className="flex h-full flex-col">
      <div data-type="body-s" className="shrink-0 border-b border-outline-variant/40 bg-surface-low/40 px-3 py-1.5 text-on-surface-var">
        {
}
        <span className="font-mono">{rel}</span>{' '}
        {
}
        <span data-type="caption" className="text-on-surface-low">· {
          deleted ? 'deleted — removed from working tree'
          : original === '' && modified !== '' ? 'new file — not yet committed'
          : original === '' && modified === '' ? 'empty file — nothing to compare'
          : 'working vs HEAD'}</span>
        {truncated && (
          <span data-type="caption" className="ml-2" style={{ color: 'var(--color-warn)' }}>
            · large file — diff truncated at 512&nbsp;KB; later changes aren't shown
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1">
        <Suspense fallback={<div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>}>
          <MonacoDiff
            onMount={onDiffMount}
            original={original} modified={modified}
            language={monacoLang(name)}
            theme={mode === 'light' ? 'light' : 'vs-dark'}
            options={{ readOnly: true, renderSideBySide: true, renderSideBySideInlineBreakpoint: 700, automaticLayout: true, fontSize: 13, minimap: { enabled: false }, ignoreTrimWhitespace: false }}
          />
        </Suspense>
      </div>
    </div>
  )
}
