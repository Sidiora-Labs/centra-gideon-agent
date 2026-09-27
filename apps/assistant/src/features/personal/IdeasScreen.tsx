import React, { useEffect, useMemo, useRef, useState } from 'react'
import { gatewayJson, GatewayError } from '../../shared/transport.web'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { createShellRoute } from '../../shared/shell/shellRoutes'
import type { ModuleProps } from '../../shared/shell/webModules.web'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import { createPersonalClient } from './client'
import { projectIdeas, projectSuggestionPrompts } from './ideaProjection'

type ReadState = 'loading' | 'ready' | 'denied' | 'unavailable' | 'error'

export function IdeasScreen({ route, scope, navigate, onReturn }: ModuleProps) {
  const { palette } = useShellTheme()
  const client = useMemo(() => createPersonalClient(scope), [scope.cacheKey])
  const viewKey = `${scope.cacheKey}:${route.destination}:${route.placement?.id ?? ''}`
  const currentView = useRef(viewKey)
  currentView.current = viewKey
  const [result, setResult] = useState<{ key: string; ideas: ReturnType<typeof projectIdeas>; prompts: ReturnType<typeof projectSuggestionPrompts>; ideaState: ReadState; ideaError: string; promptError: string } | null>(null)
  const [draftValue, setDraftValue] = useState<{ key: string; value: string } | null>(null)
  const ideas = result?.key === viewKey ? result.ideas : []
  const prompts = result?.key === viewKey ? result.prompts : []
  const ideaState = result?.key === viewKey ? result.ideaState : 'loading'
  const ideaError = result?.key === viewKey ? result.ideaError : ''
  const promptError = result?.key === viewKey ? result.promptError : ''
  const draft = draftValue?.key === viewKey ? draftValue.value : ''
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let current = true
    const requestKey = viewKey
    let nextResult = { key: requestKey, ideas: [] as ReturnType<typeof projectIdeas>, prompts: [] as ReturnType<typeof projectSuggestionPrompts>, ideaState: 'loading' as ReadState, ideaError: '', promptError: '' }
    void client.readIdeas(controller.signal).then(rows => {
      if (!current || currentView.current !== requestKey) return
      nextResult = { ...nextResult, ideas: projectIdeas(rows), ideaState: 'ready' }
      setResult(nextResult)
    }).catch((error: unknown) => {
      if (!current || currentView.current !== requestKey) return
      nextResult = { ...nextResult, ideaState: error instanceof GatewayError && error.status === 403 ? 'denied' : error instanceof GatewayError && [404, 501].includes(error.status) ? 'unavailable' : 'error', ideaError: error instanceof Error ? error.message : 'Ideas could not be loaded.' }
      setResult(nextResult)
    })
    void gatewayJson<unknown>('/api/suggestions', { signal: controller.signal }).then(payload => {
      if (current && currentView.current === requestKey) {
        nextResult = { ...nextResult, prompts: projectSuggestionPrompts(payload) }
        setResult(nextResult)
      }
    }).catch((error: unknown) => {
      if (current && currentView.current === requestKey && !controller.signal.aborted) {
        nextResult = { ...nextResult, promptError: error instanceof Error ? error.message : 'Prompt suggestions could not be loaded.' }
        setResult(nextResult)
      }
    })
    return () => { current = false; controller.abort() }
  }, [client, attempt, viewKey])

  useEffect(() => () => client.dispose(), [client])
  const style = {
    '--idea-text': palette.text, '--idea-muted': palette.muted, '--idea-line': palette.line,
    '--idea-card': palette.card, '--idea-canvas': palette.canvas, '--idea-accent': palette.blueDark,
    '--idea-accent-surface': palette.sky,
  } as React.CSSProperties
  const state = ideaState === 'loading' ? { kind: 'loading' as const, message: 'Loading saved Ideas…' }
    : ideaState === 'denied' ? { kind: 'denied' as const, message: 'Access to saved Ideas was denied for this session.' }
      : ideaState === 'unavailable' ? { kind: 'error' as const, message: 'Saved Ideas are unavailable on this Gideon instance.', onRetry: () => setAttempt(value => value + 1) }
        : ideaState === 'error' ? { kind: 'error' as const, message: ideaError || 'Saved Ideas could not be loaded.', onRetry: () => setAttempt(value => value + 1) }
          : ideas.length === 0 ? { kind: 'empty' as const, message: 'No saved Ideas yet.', action: <p>Idea lists saved in your Gideon knowledge store will appear here.</p> }
            : { kind: 'ready' as const }

  return <WorkspaceFrame route={route} mode="full" title="Ideas" state={state} onBack={onReturn}
    actions={<button type="button" onClick={() => setAttempt(value => value + 1)} disabled={ideaState === 'loading'}>Refresh</button>}>
    <div className="gideon-ideas" style={style}>
      <section className="gideon-ideas__prompts" aria-labelledby="ideas-prompts-title">
        <div><h2 id="ideas-prompts-title">Start with a prompt</h2><p>Suggestions are cached prompts. They do not represent saved or reviewed Ideas.</p></div>
        {promptError && <p role="status" className="gideon-ideas__notice">Prompt suggestions could not be refreshed: {promptError}</p>}
        {prompts.length > 0 && <div className="gideon-ideas__chips" aria-label="Cached prompt suggestions">
          {prompts.map((prompt, index) => <button key={`${prompt.generatedAt}:${index}:${prompt.text}`} type="button" onClick={() => setDraftValue({ key: viewKey, value: prompt.text })}>{prompt.text}</button>)}
        </div>}
        {prompts.some(prompt => prompt.stale) && <p className="gideon-ideas__meta">These prompts may be stale. Review and edit one before using it.</p>}
        <label htmlFor="gideon-ideas-draft">Prompt draft</label>
        <textarea id="gideon-ideas-draft" rows={3} value={draft} onChange={event => setDraftValue({ key: viewKey, value: event.currentTarget.value })} placeholder="Choose a prompt or write your own" />
      </section>
      <section className="gideon-ideas__saved" aria-labelledby="ideas-saved-title">
        <h2 id="ideas-saved-title">Saved from your knowledge</h2>
        {ideaState === 'ready' && ideas.length === 0 && <p>No saved idea-list items are available yet.</p>}
        {ideaState === 'ready' && ideas.map(idea => <article className="gideon-ideas__card" key={`${idea.listId}:${idea.memberId}`}>
          <header><div><h3>{idea.title}</h3><p className="gideon-ideas__meta">{idea.listTitle} · {idea.freshness === 'stale' ? 'Earlier result; refresh to confirm' : 'Current source'}</p></div>
            <span className="gideon-ideas__status">{idea.sourceStatus}</span></header>
          <p><strong>Reason</strong> · {idea.reason}</p>
          <blockquote>{idea.evidence}</blockquote>
          <dl><div><dt>Decision</dt><dd>No accept or dismiss decision is recorded.</dd></div>
            <div><dt>Revision</dt><dd>{idea.revision !== undefined ? `Revision ${idea.revision}` : idea.revisionHash ? 'Content revision recorded' : 'Revision not supplied'}</dd></div></dl>
          <details className="gideon-ideas__record-details">
            <summary>Source and record details</summary>
            <dl>
              <div><dt>Source kind</dt><dd>{idea.sourceKind}</dd></div>
              <div><dt>Source ID</dt><dd><code>{idea.sourceId}</code></dd></div>
              <div><dt>List ID</dt><dd><code>{idea.listId}</code></dd></div>
              <div><dt>Member ID</dt><dd><code>{idea.memberId}</code></dd></div>
              <div><dt>Content revision reference</dt><dd>{idea.revisionHash ? <code>{idea.revisionHash}</code> : 'Not supplied'}</dd></div>
            </dl>
          </details>
          <button type="button" onClick={() => navigate(createShellRoute('apps', { view: 'detail', record: { kind: 'knowledge', id: idea.sourceId }, placement: { id: 'knowledge/item' }, returnTo: { destination: route.destination, record: route.record, placement: route.placement, sessionId: route.sessionId } }))}>Open source</button>
        </article>)}
      </section>
      <style>{`
        .gideon-ideas{box-sizing:border-box;min-height:100%;padding:clamp(18px,4vw,36px);color:var(--idea-text)}
        .gideon-ideas h2{margin:0 0 6px;font-size:1.18rem}.gideon-ideas p{line-height:1.55}.gideon-ideas__prompts,.gideon-ideas__card{padding:18px;border:1px solid var(--idea-line);border-radius:14px;background:var(--idea-card)}
        .gideon-ideas__prompts>div:first-child p,.gideon-ideas__meta{margin:4px 0;color:var(--idea-muted);font-size:.92rem}.gideon-ideas__chips{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}.gideon-ideas button{border:1px solid var(--idea-line);border-radius:9px;padding:8px 12px;background:var(--idea-canvas);color:inherit;font:inherit;cursor:pointer}.gideon-ideas button:focus-visible,.gideon-ideas textarea:focus-visible{outline:2px solid var(--idea-accent);outline-offset:2px}.gideon-ideas__chips button:hover{border-color:var(--idea-accent);background:var(--idea-accent-surface)}
        .gideon-ideas label{display:block;margin:14px 0 6px}.gideon-ideas textarea{box-sizing:border-box;width:100%;resize:vertical;padding:10px;border:1px solid var(--idea-line);border-radius:9px;background:var(--idea-canvas);color:inherit;font:inherit}.gideon-ideas__prompts>button{margin-top:10px}.gideon-ideas__saved{display:grid;gap:12px;margin-top:24px}.gideon-ideas__saved>h2{margin-bottom:2px}.gideon-ideas__card header{display:flex;justify-content:space-between;gap:12px}.gideon-ideas__card h3{margin:0}.gideon-ideas__status{align-self:start;padding:4px 8px;border:1px solid var(--idea-line);border-radius:99px;color:var(--idea-muted);font-size:.82rem}.gideon-ideas blockquote{margin:12px 0;padding:10px 14px;border-left:3px solid var(--idea-accent);background:var(--idea-canvas);white-space:pre-wrap;overflow-wrap:anywhere}.gideon-ideas dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}.gideon-ideas dl div{min-width:0}.gideon-ideas dt{color:var(--idea-muted);font-size:.82rem}.gideon-ideas dd{margin:3px 0;overflow-wrap:anywhere}.gideon-ideas code{white-space:normal}.gideon-ideas__record-details{margin-top:10px;border-top:1px solid var(--idea-line);padding-top:10px}.gideon-ideas__record-details summary{cursor:pointer;color:var(--idea-muted)}.gideon-ideas__notice{color:var(--idea-muted)}
        @media(max-width:600px){.gideon-ideas{padding:16px}.gideon-ideas__card header{display:block}.gideon-ideas__status{display:inline-block;margin-top:8px}}
      `}</style>
    </div>
  </WorkspaceFrame>
}

export default IdeasScreen
