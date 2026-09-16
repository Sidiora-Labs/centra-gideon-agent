import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { FileDiff, Inbox, Package, TriangleAlert, Upload } from 'lucide-react'
import { SidePanel } from '../../shared/ui/SidePanel'
import { Segmented } from '../../shared/ui/Segmented'
import { ListSkeleton, Skeleton } from '../../shared/ui/ListScaffold'
import { InlineError } from '../../shared/ui/InlineError'
import { QuietButton } from '../../shared/ui/QuietButton'
import { Button } from '../../shared/ui/Button'
import { ContentSurface } from '../../shared/ui/content/ContentSurface'
import { resolveContentType } from '../../shared/ui/content/contentTypes'
import {
  api,
  ApiError,
  type Artifact,
  type WorkflowDropStatus,
  type WorkflowOutboxEntry,
} from '../../shared/data/api'

const ArtifactCompare = lazy(() =>
  import('../artifacts/ArtifactCompare').then((m) => ({ default: m.ArtifactCompare })),
)

export function OutboxPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [files, setFiles] = useState<WorkflowOutboxEntry[] | null>(null)
  const [drop, setDrop] = useState<WorkflowDropStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string>('')
  const [tab, setTab] = useState<'rendered' | 'source' | 'compare'>('rendered')
  const [detail, setDetail] = useState<Artifact | null>(null)
  const [versions, setVersions] = useState<number[]>([])
  const [detailError, setDetailError] = useState('')
  const [dropBusy, setDropBusy] = useState(false)
  const [dropNote, setDropNote] = useState('')
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    Promise.all([api.workflowRunOutbox(runId), api.workflowRunDropStatus(runId)])
      .then(([out, st]) => {
        if (!live) return
        setFiles(out.files)
        setDrop(st)
      })
      .catch((e) => {
        if (!live) return
        if (e instanceof ApiError && e.status === 404) {
          setError('This run could not be found. It may have been deleted.')
        } else {
          setError(e instanceof Error ? e.message : 'Could not read this run’s artifacts.')
        }
      })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  useEffect(() => {
    if (!selected) { setDetail(null); setVersions([]); return }
    let live = true
    setDetailError('')
    Promise.all([api.artifact(selected), api.artifactVersions(selected)])
      .then(([art, v]) => { if (live) { setDetail(art); setVersions(v.versions ?? []) } })
      .catch((e) => {
        if (live) setDetailError(e instanceof Error ? e.message : 'Could not load this artifact.')
      })
    return () => { live = false }
  }, [selected])

  const ctype = useMemo(
    () => (detail ? resolveContentType({ kind: detail.kind }) : null),
    [detail],
  )

  async function sendFiles(picked: File[]) {
    if (!picked.length) return
    setDropBusy(true)
    setDropNote('')
    try {
      const st = await api.workflowRunDrop(runId, picked)
      setDrop(st)
      setDropNote(`Accepted ${picked.length === 1 ? picked[0].name : `${picked.length} files`}.`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 428) {
        const names = picked.map((f) => f.name).join(', ')
        const total = picked.reduce((n, f) => n + f.size, 0)
        const { confirm } = await import('../../shared/ui/dialog')
        const ok = await confirm({
          title: 'Hand these files to the run?',
          body: `${names} (${fmtBytes(total)}). The run can read them but not change them.`,
          confirmLabel: 'Approve',
        })
        if (!ok) { setDropNote('Nothing was handed over.'); setDropBusy(false); return }
        try {
          setDrop(await api.workflowRunDrop(runId, picked, true))
          setDropNote(`Accepted ${picked.length === 1 ? picked[0].name : `${picked.length} files`}.`)
        } catch (inner) {
          setDropNote(inner instanceof Error ? inner.message : 'The drop failed.')
        }
      } else {
        setDropNote(e instanceof Error ? e.message : 'The drop failed.')
      }
    } finally {
      setDropBusy(false)
    }
  }

  return (
    <SidePanel
      fillHeight
      title="Artifacts"
      icon={<Package size={18} />}
      onClose={onClose}
    >
      {loading ? (
        <ListSkeleton rows={3} />
      ) : error ? (
        <div className="p-m"><InlineError icon multiline>{error}</InlineError></div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-m p-m">
          <section className="flex flex-col gap-xs">
            <h3 data-type="label-s" className="fw-500">Published by this run</h3>
            {!files?.length ? (
              <p data-type="body-s" className="text-on-surface-low">
                Nothing published yet. A node that declares <code>publish:</code> registers its
                output here as an artifact.
              </p>
            ) : (
              <ul className="flex flex-col gap-xs">
                {files.map((f) => (
                  <li key={f.slug}>
                    <Button
                      variant="ghost"
                      size="xs"
                      shape="squircle"
                      onClick={() => setSelected(f.slug === selected ? '' : f.slug)}
                      ariaPressed={f.slug === selected}
                      className={`w-full justify-start gap-2 px-2${f.slug === selected ? ' bg-surface-high' : ''}`}
                    >
                      <span className="min-w-0 flex-1 truncate">{f.artifact || f.slug}</span>
                      {
}
                      <span data-type="caption" className="shrink-0 text-on-surface-low">
                        {f.action === 'noop' ? 'unchanged' : f.action}
                      </span>
                      {!f.self_contained && (
                        <span
                          data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-warning"
                          title="A referenced local file could not be copied in, so this version depends on the workspace still existing."
                        >
                          <TriangleAlert size={12} aria-hidden="true" />
                          not self-contained
                        </span>
                      )}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {selected && (
            <section className="flex min-h-0 flex-1 flex-col gap-xs">
              {detailError ? (
                <InlineError icon multiline>{detailError}</InlineError>
              ) : !detail || !ctype ? (
                <Skeleton className="h-24 w-full" />
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <Segmented
                      ariaLabel="Artifact view"
                      value={tab}
                      onChange={(v) => setTab(v as 'rendered' | 'source' | 'compare')}
                      options={[
                        { key: 'rendered', label: 'Rendered' },
                        { key: 'source', label: 'Source' },
                        ...(versions.length > 1
                          ? [{ key: 'compare', label: 'Compare' }]
                          : []),
                      ]}
                    />
                    {files?.find((f) => f.slug === selected)?.change_note && (
                      <span data-type="caption" className="min-w-0 truncate text-on-surface-low">
                        {files.find((f) => f.slug === selected)?.change_note}
                      </span>
                    )}
                  </div>
                  {
}
                  <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline/40">
                    {tab === 'compare' ? (
                      <Suspense fallback={<Skeleton className="h-full w-full" />}>
                        <ArtifactCompare art={detail} versions={versions} />
                      </Suspense>
                    ) : (
                      <ContentSurface
                        type={ctype}
                        content={detail.content ?? ''}
                        title={detail.name || selected}
                        docId={selected}
                        readOnly
                        initialView={tab === 'source' ? 'edit' : 'preview'}
                        key={tab}
                      />
                    )}
                  </div>
                </>
              )}
            </section>
          )}

          <section className="flex flex-col gap-xs border-t border-outline/40 pt-m">
            <h3 data-type="label-s" className="flex items-center gap-2 fw-500">
              <Inbox size={14} aria-hidden="true" />
              Hand files to this run
            </h3>
            {!drop?.enabled ? (
              <p data-type="body-s" className="text-on-surface-low">
                {drop?.reason || 'This run does not accept files.'}
              </p>
            ) : (
              <>
                {
}
                <QuietButton
                  onClick={() => fileInputRef.current?.click()}
                  title="Choose files to hand to this run"
                  disabled={dropBusy}
                  disabledReason="A hand-over is already in flight"
                >
                  <Upload size={13} /> {dropBusy ? 'Handing over…' : 'Choose files'}
                </QuietButton>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  disabled={dropBusy}
                  aria-label="Choose files to hand to this run"
                  className="sr-only"
                  onChange={(e) => {
                    const picked = Array.from(e.target.files ?? [])
                    e.target.value = ''
                    void sendFiles(picked)
                  }}
                />
                {dropNote && <p data-type="caption" className="text-on-surface-low">{dropNote}</p>}
                {drop.files.length > 0 && (
                  <ul className="flex flex-col gap-xs">
                    {drop.files.map((f) => (
                      <li
                        key={f.filename}
                        data-type="caption" className="flex items-center gap-2 text-on-surface-low"
                      >
                        <FileDiff size={12} aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate">{f.filename}</span>
                        <span className="shrink-0 tabular-nums">{fmtBytes(f.size)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </SidePanel>
  )
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}
