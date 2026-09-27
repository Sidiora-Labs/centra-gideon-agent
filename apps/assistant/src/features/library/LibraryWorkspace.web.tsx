import React, { useEffect, useMemo, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import { WorkspaceFrame, type WorkspaceFrameState } from "../../shared/shell/WorkspaceFrame.web";
import { createShellRoute, type ShellReturnContext, type ShellRoute } from "../../shared/shell/shellRoutes";
import { getLibraryRecord, listKnowledgeItems, LibraryReadError, type KnowledgeItem, type KnowledgeList } from "./libraryApi";
import { libraryHomeRoute, libraryItemRoute, libraryRecordHref, parseLibraryRecord, type LibraryRecordRef } from "./libraryRoutes";

export type LibraryWorkspaceProps = {
  route: ShellRoute;
  scope: OwnerScope;
  navigate: (route: ShellRoute) => void;
  onReturn?: () => void;
  returnTo?: ShellReturnContext;
};

type LoadState<T> = { scopeKey: string; value?: T; error?: LibraryReadError; loading: boolean };

export function LibraryWorkspace({ route, scope, navigate, onReturn, returnTo }: LibraryWorkspaceProps) {
  const record = useMemo(() => parseLibraryRecord(route), [route]);
  const [listState, setListState] = useState<LoadState<KnowledgeList>>({ scopeKey: "", loading: true });
  const [itemState, setItemState] = useState<LoadState<KnowledgeItem>>({ scopeKey: "", loading: true });
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (record) {
      const controller = new AbortController();
      setItemState(current => ({ scopeKey: scope.cacheKey, value: current.scopeKey === scope.cacheKey ? current.value : undefined, loading: true }));
      void getLibraryRecord(scope, record, controller.signal).then(value => {
        setItemState({ scopeKey: scope.cacheKey, value, loading: false });
      }).catch((error: unknown) => {
        if (!controller.signal.aborted) setItemState(current => ({ scopeKey: scope.cacheKey,
          value: current.scopeKey === scope.cacheKey ? current.value : undefined,
          error: error instanceof LibraryReadError ? error : new LibraryReadError("failed", "The Library record could not be opened."), loading: false }));
      });
      return () => controller.abort();
    }
    const controller = new AbortController();
    setListState(current => ({ scopeKey: scope.cacheKey, value: current.scopeKey === scope.cacheKey ? current.value : undefined, loading: true }));
    void listKnowledgeItems(scope, controller.signal).then(value => {
      setListState({ scopeKey: scope.cacheKey, value, loading: false });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setListState(current => ({ scopeKey: scope.cacheKey,
        value: current.scopeKey === scope.cacheKey ? current.value : undefined,
        error: error instanceof LibraryReadError ? error : new LibraryReadError("failed", "The Library could not be loaded."), loading: false }));
    });
    return () => controller.abort();
  }, [record?.kind, record?.id, scope.cacheKey, reload]);

  const activeList = listState.scopeKey === scope.cacheKey ? listState : { scopeKey: scope.cacheKey, loading: true };
  const activeItem = itemState.scopeKey === scope.cacheKey ? itemState : { scopeKey: scope.cacheKey, loading: true };
  const error = record ? activeItem.error : activeList.error;
  const hasCurrentData = record ? !!activeItem.value : !!activeList.value;
  const stale = !!error && hasCurrentData;
  const frameState: WorkspaceFrameState = error?.kind === "forbidden" && !hasCurrentData
    ? { kind: "denied", message: error.message }
    : !error && !hasCurrentData && ((record ? activeItem.loading : activeList.loading))
      ? { kind: "loading", message: record ? "Opening knowledge item…" : "Loading your Library…" }
      : error && !hasCurrentData && error.kind !== "missing" && error.kind !== "forbidden"
        ? { kind: "ready" }
        : !record && activeList.value?.items.length === 0
          ? { kind: "empty", message: "Your Library is empty.", action: <p>Knowledge items saved in Gideon will appear here.</p> }
          : { kind: "ready" };

  function retry() { setReload(value => value + 1); }
  function goBack() {
    if (onReturn) return onReturn();
    const target = returnTo ?? route.returnTo;
    if (target) navigate(createShellRoute(target.destination, {
      view: target.record ? "detail" : target.placement ? "workspace" : "list",
      record: target.record,
      placement: target.placement,
      sessionId: target.sessionId,
      ...(target.selectionId && record ? { returnTo: {
        destination: "apps", record: { kind: record.kind, id: record.id }, placement: { id: "knowledge/item" },
      } } : {}),
    }));
    else navigate(libraryHomeRoute());
  }

  const title = record ? activeItem.value?.title ?? recordTitle(record) : "Research and Knowledge";
  const libraryStateMessage = error && stale
    ? error.kind === "unavailable" ? "The knowledge service is unavailable. Showing your earlier results; they may be out of date." : "Refresh failed. Showing your earlier results; they may be out of date."
    : error?.kind === "missing" ? error.message
      : error && !hasCurrentData && error.kind !== "forbidden" ? error.message : undefined;

  return <WorkspaceFrame route={route} mode="full" title={title} state={frameState}
    onBack={record ? goBack : undefined} onGoToChat={() => navigate(createShellRoute("chat"))}
    actions={!record ? <button type="button" onClick={retry} disabled={activeList.loading}>Refresh</button> : undefined}>
    <style>{`
      .gideon-library { display:grid; grid-template-columns:minmax(220px, 280px) minmax(0, 1fr); min-height:100%; color:var(--gideon-text, #e8eaf0); background:var(--gideon-surface, #11151d); }
      .gideon-library__rail { padding:24px 18px; border-right:1px solid var(--gideon-border, #2a303a); }
      .gideon-library__main { min-width:0; padding:clamp(20px, 4vw, 44px); }
      .gideon-library__eyebrow { color:var(--gideon-muted, #9ba4b2); font-size:12px; letter-spacing:.08em; text-transform:uppercase; }
      .gideon-library__list { display:grid; gap:8px; margin:18px 0 0; padding:0; list-style:none; }
      .gideon-library__link { display:block; padding:14px 16px; border:1px solid var(--gideon-border, #2a303a); border-radius:12px; color:inherit; text-decoration:none; background:var(--gideon-card, #191f29); }
      .gideon-library__link:hover,.gideon-library__link:focus-visible { border-color:var(--gideon-accent, #92adff); outline:2px solid transparent; }
      .gideon-library__meta { display:block; margin-top:5px; color:var(--gideon-muted, #9ba4b2); font-size:13px; }
      .gideon-library__content { line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }
      .gideon-library__notice { margin:0 0 18px; padding:12px 14px; border-radius:10px; background:#38321f; color:#f2d99a; }
      .gideon-library__actions { display:flex; gap:10px; margin-bottom:16px; }
      @media(max-width:700px) { .gideon-library { display:block; min-height:100dvh; } .gideon-library__rail { display:none; } .gideon-library__main { padding:18px 16px 36px; } .gideon-library__link { padding:16px; } }
    `}</style>
    {libraryStateMessage && <p className="gideon-library__notice" role={stale ? "status" : "alert"} data-library-state={stale ? "stale" : error?.kind}>
      {libraryStateMessage}{(stale || !hasCurrentData) && <> <button type="button" onClick={retry}>{stale ? "Retry refresh" : "Retry"}</button></>}
    </p>}
    {!record && activeList.value && <LibraryList items={activeList.value.items} route={route} navigate={navigate} />}
    {record && activeItem.value && <LibraryReader item={activeItem.value} />}
    {record && error?.kind === "missing" && !activeItem.value && <section role="alert" data-library-state="missing">
      <h2>Knowledge item unavailable</h2><p>{error.message}</p><button type="button" onClick={goBack}>Return to Library</button>
    </section>}
    {record && error?.kind === "forbidden" && activeItem.value && <p className="gideon-library__notice" role="alert">Access to this Library record was denied.</p>}
  </WorkspaceFrame>;
}

export function LibraryItemLink({ item, origin, navigate }: { item: KnowledgeItem; origin?: ShellRoute; navigate: (route: ShellRoute) => void }) {
  const href = libraryRecordHref({ kind: "knowledge", id: item.id }, origin);
  return <a className="gideon-library__link" href={href} onClick={event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(libraryItemRoute({ kind: "knowledge", id: item.id }, origin));
  }}>
    <strong>{item.title}</strong><span className="gideon-library__meta">{item.item_type ?? item.kind ?? "Knowledge item"}{item.provider ? ` · ${item.provider}` : ""}</span>
  </a>;
}

function LibraryList({ items, route, navigate }: { items: readonly KnowledgeItem[]; route: ShellRoute; navigate: (route: ShellRoute) => void }) {
  return <div className="gideon-library">
    <aside className="gideon-library__rail"><p className="gideon-library__eyebrow">Library</p><h2>Browse</h2>
      <nav aria-label="Research and Knowledge"><ul className="gideon-library__list"><li><a className="gideon-library__link" href="#library-items" onClick={event => { event.preventDefault(); document.getElementById("library-items")?.scrollIntoView({ behavior: "smooth" }); }}>All knowledge</a></li></ul></nav>
    </aside>
    <section className="gideon-library__main" id="library-items" aria-labelledby="library-list-title">
      <p className="gideon-library__eyebrow">Research and Knowledge</p><h2 id="library-list-title">Your Library</h2>
      <p className="gideon-library__meta">{items.length} {items.length === 1 ? "item" : "items"} available</p>
      <ul className="gideon-library__list">{items.map(item => <li key={item.id}><LibraryItemLink item={item} origin={route} navigate={navigate} /></li>)}</ul>
    </section>
  </div>;
}

function LibraryReader({ item }: { item: KnowledgeItem }) {
  return <article className="gideon-library__main" aria-labelledby="library-reader-title">
    <p className="gideon-library__eyebrow">{item.item_type ?? item.kind ?? "Knowledge item"}</p>
    <h2 id="library-reader-title">{item.title}</h2>
    {(item.source_url || item.provider || item.created_at) && <p className="gideon-library__meta">
      {item.provider ? `Source: ${item.provider}` : "Gideon knowledge"}{item.created_at ? ` · Added ${item.created_at}` : ""}
      {item.source_url && <> · <a href={item.source_url} target="_blank" rel="noreferrer">Open original source</a></>}
    </p>}
    <div className="gideon-library__content">{item.content || "This item has no extracted text. Its record remains available in your Library."}</div>
  </article>;
}

function recordTitle(record: LibraryRecordRef): string {
  return record.kind === "knowledge" ? "Knowledge item" : `${record.kind[0].toUpperCase()}${record.kind.slice(1)}`;
}
