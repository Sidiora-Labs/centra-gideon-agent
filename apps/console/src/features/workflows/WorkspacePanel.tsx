import { useEffect, useState } from 'react'
import { ExternalLink, FileDiff, FolderGit2, TriangleAlert } from 'lucide-react'
import { SidePanel } from '../../shared/ui/SidePanel'
import { Skeleton, LoadingStatus } from '../../shared/ui/ListScaffold'
import { InlineError } from '../../shared/ui/InlineError'
import { api, ApiError, type WorkflowWorkspaceReview } from '../../shared/data/api'

export function WorkspacePanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [data, setData] = useState<WorkflowWorkspaceReview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    setData(null)
    api.workflowRunWorkspace(runId)
      .then((d) => { if (live) setData(d) })
      .catch((e) => {
        if (!live) return
        if (e instanceof ApiError && e.status === 404) {
          setError('This run could not be found. It may have been deleted.')
        } else {
          setError(e instanceof Error ? e.message : 'Could not read this run’s workspace.')
        }
      })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  return (
    <SidePanel
      fillHeight
      storeKey="wf-workspace-w"
      icon={<FolderGit2 size={18} className="text-primary" />}
      title="Workspace"
      onClose={onClose}
    >
      <div data-testid="workspace-panel-body" className="flex flex-col gap-l">
        {loading ? (
          <div role="status" aria-busy="true"  className="flex flex-col gap-l">
        <LoadingStatus what="the run’s workspace" />
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : error ? (
          <InlineError icon multiline>{error}</InlineError>
        ) : data ? (
          <WorkspaceBody data={data} />
        ) : null}
      </div>
    </SidePanel>
  )
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-xs">
      <h3 data-type="label-m" className="text-on-surface-low">{label}</h3>
      {children}
    </section>
  )
}

function verbCommand(verb: string, branch: string): string {
  return verb === 'checkout_branch' ? `git checkout ${branch}` : `git merge --squash ${branch}`
}

function WorkspaceBody({ data }: { data: WorkflowWorkspaceReview }) {
  const { workspace: ws, reintegration: offer, declared, preview } = data
  const conflicts = offer?.conflicts ?? []
  const setup = declared?.setup

  if (!ws?.path && !ws?.branch) {
    return (
      <p data-testid="workspace-none" data-type="body-s" className="text-on-surface-low">
        This run did not declare a workspace, so it worked in place and has no isolated diff to
        review. Add a <code className="font-mono">workspace:</code> block to the template to run it
        in a git worktree or a scratch directory.
      </p>
    )
  }

  return (
    <>
      <div data-type="caption" className="flex flex-wrap items-center gap-m">
        {declared?.mode && (
          <span data-testid="workspace-mode" data-type="caption" className="inline-flex items-center rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-var">
            {declared.mode}
          </span>
        )}
        <span
          data-testid="workspace-liveness"
          className={ws.alive ? 'text-on-surface' : 'text-on-surface-low'}
          title={ws.alive ? 'The workspace is still on disk' : 'The workspace has been removed'}
        >
          {ws.alive ? 'on disk' : 'removed'}
        </span>
        {ws.dirty && <span className="text-on-surface">uncommitted changes</span>}
      </div>

      {
}
      {declared?.degraded_reason && (
        <p data-testid="workspace-degraded" data-type="caption" className="flex items-start gap-xs text-on-surface-low">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          {declared.degraded_reason}
        </p>
      )}

      {
}
      {preview && (
        <Block label="Preview">
          {preview.ports.length > 0 ? (
            <ul data-testid="preview-ports" className="flex flex-col gap-xs">
              {preview.ports.map((p) => (
                <li key={p.port} data-type="caption" className="flex items-center gap-s rounded-md bg-surface px-2.5 py-1.5">
                  <span className="min-w-0 flex-1 truncate font-mono text-on-surface">{p.url}</span>
                  {p.command && (
                    <span data-type="caption" className="shrink-0 truncate font-mono text-on-surface-low">{p.command}</span>
                  )}
                  {
}
                  <a
                    href={p.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    data-type="caption" className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-on-surface-low hover:bg-surface-high hover:text-on-surface"
                    aria-label={`Open Preview on port ${p.port}`}
                    title={`Open localhost:${p.port} in a new tab`}
                  >
                    <ExternalLink size={13} /> Open Preview
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p data-testid="preview-none" data-type="caption" className="text-on-surface-low">
              {preview.reason || 'No dev server is listening in this run’s workspace.'}
            </p>
          )}
          <p data-type="caption" className="text-on-surface-low">
            Local only — this machine, no tunnel and no sharing. Links are checked each time you
            open this panel, so a server that has stopped disappears from the list.
          </p>
        </Block>
      )}

      {ws.preserved_workspace_path && (
        <Block label="Preserved workspace">
          <p data-testid="preserved-path" data-type="caption" className="break-all rounded-md bg-surface px-3 py-2 font-mono text-on-surface">
            {ws.preserved_workspace_path}
          </p>
        </Block>
      )}

      <Block label={`Changed files${ws.changed.length ? ` (${ws.changed.length})` : ''}`}>
        {ws.changed.length === 0 ? (
          <p data-type="caption" className="text-on-surface-low">
            {ws.alive ? 'Nothing changed in the workspace.' : 'The workspace is gone; its work is on the branch below.'}
          </p>
        ) : (
          <ul data-testid="changed-files" className="flex max-h-72 flex-col gap-xs overflow-auto">
            {ws.changed.map((c) => (
              <li key={c.path} data-type="caption" className="flex items-center gap-s rounded-md bg-surface px-2.5 py-1.5">
                <FileDiff size={13} className="shrink-0 text-on-surface-low" />
                <span className="min-w-0 flex-1 truncate font-mono text-on-surface">{c.path}</span>
                <span className="shrink-0 text-on-surface-low">{c.status}</span>
                {
}
                {c.staged && <span data-type="caption" className="shrink-0 text-on-surface-var">staged</span>}
              </li>
            ))}
          </ul>
        )}
      </Block>

      {
}
      {setup?.failed?.length ? (
        <Block label={`Setup failures (${setup.failed.length})`}>
          <ul data-testid="setup-failed" className="flex flex-col gap-xs">
            {setup.failed.map((f, i) => (
              <li key={i} data-type="caption" className="rounded-md bg-surface px-2.5 py-1.5 font-mono text-on-surface-var break-words">
                {f}
              </li>
            ))}
          </ul>
          <p data-type="caption" className="text-on-surface-low">
            Setup never blocks a run — these are recorded so a stage that failed on a missing
            dependency has an explanation in reach.
          </p>
        </Block>
      ) : null}

      {conflicts.length > 0 && (
        <Block label={`Conflicts (${conflicts.length})`}>
          <ul data-testid="workspace-conflicts" className="flex flex-col gap-xs">
            {conflicts.map((p) => (
              <li key={p} data-type="caption" className="rounded-md bg-surface px-2.5 py-1.5 font-mono text-on-surface break-all">{p}</li>
            ))}
          </ul>
        </Block>
      )}

      {offer?.verbs?.length ? (
        <Block label="Take this work">
          <ul data-testid="reintegration-verbs" className="flex flex-col gap-s">
            {offer.verbs.map((v) => (
              <li key={v.verb} className="flex flex-col gap-xs rounded-md bg-surface px-3 py-2">
                <div className="flex items-center gap-s">
                  <span data-type="body-s" className="text-on-surface">{v.label}</span>
                  {!v.safe && (
                    <span data-testid={`unsafe-${v.verb}`} data-type="caption" className="text-on-surface-low">
                      conflicts
                    </span>
                  )}
                </div>
                <p data-type="caption" className="text-on-surface-low">{v.detail}</p>
                <code data-type="caption" className="break-all font-mono text-on-surface-var">
                  {verbCommand(v.verb, offer.branch)}
                </code>
              </li>
            ))}
          </ul>
          <p data-testid="reintegration-note" data-type="caption" className="text-on-surface-low">{offer.note}</p>
        </Block>
      ) : null}
    </>
  )
}
