import React, { useEffect, useMemo, useRef, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import { WorkspaceFrame, type WorkspaceFrameState } from "../../shared/shell/WorkspaceFrame.web";
import { createShellRoute, type ShellReturnContext, type ShellRoute } from "../../shared/shell/shellRoutes";
import { getLibraryRecord, LibraryReadError, type KnowledgeItem } from "./libraryApi";
import { libraryHomeRoute, libraryItemRoute, libraryRecordHref, parseLibraryRecord, type LibraryRecordRef } from "./libraryRoutes";
import { useShellTheme } from "../../shared/shell/shellTheme";
import { LibraryHome } from "./LibraryHome.web";
import { KnowledgeReader } from "./KnowledgeReader.web";

export type LibraryWorkspaceProps = {
  route: ShellRoute;
  scope: OwnerScope;
  navigate: (route: ShellRoute) => void;
  onReturn?: () => void;
  returnTo?: ShellReturnContext;
};

type LoadState<T> = { key: string; value?: T; error?: LibraryReadError; loading: boolean };

export function LibraryWorkspace({ route, scope, navigate, onReturn, returnTo }: LibraryWorkspaceProps) {
  const record = useMemo(() => parseLibraryRecord(route), [route]);
  const { palette } = useShellTheme();
  const activeOwnerScope = useRef(scope.cacheKey);
  activeOwnerScope.current = scope.cacheKey;
  const itemKey = `${scope.cacheKey}\u0000${record ? `${record.kind}:${record.id}` : "no-record"}`;
  const [itemState, setItemState] = useState<LoadState<KnowledgeItem>>({ key: "", loading: true });
  const [reload, setReload] = useState(0);
  const requestGeneration = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    const isCurrent = () => !controller.signal.aborted && requestGeneration.current === generation;
    if (record) {
      setItemState(current => ({ key: itemKey, value: current.key === itemKey ? current.value : undefined, loading: true }));
      void getLibraryRecord(scope, record, controller.signal).then(value => {
        if (isCurrent()) setItemState({ key: itemKey, value, loading: false });
      }).catch((error: unknown) => {
        if (!isCurrent()) return;
        const readError = error instanceof LibraryReadError ? error : new LibraryReadError("failed", "The Library record could not be opened.");
        setItemState(current => ({ key: itemKey,
          ...(readError.kind === "unavailable" && current.key === itemKey && current.value ? { value: current.value } : {}),
          error: readError, loading: false }));
      });
      return () => controller.abort();
    }
    return () => controller.abort();
  }, [record?.kind, record?.id, scope.cacheKey, itemKey, reload]);

  const activeItem = itemState.key === itemKey ? itemState : { key: itemKey, loading: true };
  const error = record ? activeItem.error : undefined;
  const hasCurrentData = record ? !!activeItem.value : false;
  const stale = error?.kind === "unavailable" && hasCurrentData;
  const frameState: WorkspaceFrameState = error?.kind === "forbidden" && !hasCurrentData
    ? { kind: "denied", message: error.message }
    : record && !error && !hasCurrentData && activeItem.loading
      ? { kind: "loading", message: "Opening knowledge item…" }
      : error && !hasCurrentData && error.kind !== "missing" && error.kind !== "forbidden"
        ? { kind: "ready" }
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

  const libraryPalette = {
    "--gideon-text": palette.text,
    "--gideon-surface": palette.canvas,
    "--gideon-card": palette.card,
    "--gideon-border": palette.line,
    "--gideon-muted": palette.muted,
    "--gideon-accent": palette.blueDark,
    "--gideon-notice": palette.orange,
    "--gideon-notice-text": palette.text,
  } as React.CSSProperties;

  return <WorkspaceFrame route={route} mode="full" title={title} state={frameState}
    onBack={record ? goBack : undefined} onGoToChat={() => navigate(createShellRoute("chat"))}
    actions={record && <button type="button" onClick={retry} disabled={activeItem.loading}>Refresh</button>}>
    <style>{`
      .gideon-library { display:grid; grid-template-columns:minmax(220px, 280px) minmax(0, 1fr); min-height:100%; color:var(--gideon-text, #e8eaf0); background:var(--gideon-surface, #11151d); font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
      .gideon-library__rail { padding:24px 18px; border-right:1px solid var(--gideon-border, #2a303a); }
      .gideon-library__main { min-width:0; padding:clamp(20px, 4vw, 44px); }
      .gideon-library__eyebrow { color:var(--gideon-muted, #9ba4b2); font-size:12px; letter-spacing:.08em; text-transform:uppercase; }
      .gideon-library__list { display:grid; gap:8px; margin:18px 0 0; padding:0; list-style:none; }
      .gideon-library__link { display:block; padding:14px 16px; border:1px solid var(--gideon-border, #2a303a); border-radius:12px; color:inherit; text-decoration:none; background:var(--gideon-card, #191f29); }
      .gideon-library__link:hover,.gideon-library__link:focus-visible { border-color:var(--gideon-accent, #92adff); outline:2px solid transparent; }
      .gideon-library__meta { display:block; margin-top:5px; color:var(--gideon-muted, #9ba4b2); font-size:13px; }
      .gideon-library__content { line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }
      .gideon-library__notice { margin:0 0 18px; padding:12px 14px; border-radius:10px; background:var(--gideon-notice); color:var(--gideon-notice-text); }
      .gideon-library__actions { display:flex; gap:10px; margin-bottom:16px; }
      .gideon-library--index { display:block; }
      .gideon-library--reader { display:block; min-width:0; width:100%; }
      .gideon-library button,.gideon-library input:not([type=radio]),.gideon-library select,.gideon-library textarea { box-sizing:border-box; max-width:100%; padding:9px 12px; border:1px solid var(--gideon-border); border-radius:9px; color:var(--gideon-text); background:var(--gideon-card); font:inherit; }
      .gideon-library button { cursor:pointer; font-weight:600; }
      .gideon-library button:hover:not(:disabled) { border-color:var(--gideon-accent); }
      .gideon-library button:disabled { cursor:wait; opacity:.62; }
      .gideon-library :is(button,input,select,textarea,a,summary):focus-visible { outline:2px solid var(--gideon-accent); outline-offset:2px; }
      .gideon-library input[type=radio] { accent-color:var(--gideon-accent); }
      .gideon-library input[type=file] { width:100%; }
      .gideon-library a { color:var(--gideon-accent); }
      .gideon-library-reader { width:100%; min-width:0; }
      .gideon-library-reader__header { margin:0 0 24px; padding-bottom:20px; border-bottom:1px solid var(--gideon-border); }
      .gideon-library-reader__metadata { display:flex; flex-wrap:wrap; gap:8px 22px; margin:18px 0 0; }
      .gideon-library-reader__metadata div { min-width:0; }
      .gideon-library-reader__metadata dt { color:var(--gideon-muted); font-size:12px; }
      .gideon-library-reader__metadata dd { margin:3px 0 0; overflow-wrap:anywhere; }
      .gideon-library-reader__pdf { width:100%; }
      .gideon-library-reader__file-actions { display:flex; flex-wrap:wrap; gap:10px 18px; margin:0 0 12px; }
      .gideon-library-reader__pdf-frame { display:block; width:100%; height:min(74vh, 960px); min-height:520px; border:1px solid var(--gideon-border); border-radius:12px; background:var(--gideon-card); }
      .gideon-library-reader__extracted { margin-top:14px; }
      .gideon-library-reader__extracted summary { width:fit-content; padding:8px 0; cursor:pointer; color:var(--gideon-accent); }
      .gideon-library-reader__text-panel,.gideon-library-reader__annotations { margin-top:30px; padding-top:20px; border-top:1px solid var(--gideon-border); }
      .gideon-library-reader__text { max-width:100%; padding:22px; border:1px solid var(--gideon-border); border-radius:12px; color:var(--gideon-text); background:var(--gideon-card); line-height:1.8; white-space:pre-wrap; overflow-wrap:anywhere; user-select:text; }
      .gideon-library-reader__annotations form { display:grid; gap:10px; max-width:760px; margin-top:18px; }
      .gideon-library-reader__annotations textarea { width:100%; min-height:86px; resize:vertical; }
      .gideon-library-reader__annotations form button { justify-self:start; }
      .gideon-library-reader__annotation-list { display:grid; gap:14px; padding-left:24px; }
      .gideon-library-reader__annotation-list li { padding:14px 16px; border:1px solid var(--gideon-border); border-radius:12px; background:var(--gideon-card); }
      .gideon-library-reader__annotation-list blockquote { margin:0; padding-left:12px; border-left:3px solid var(--gideon-accent); white-space:pre-wrap; }
      @media(max-width:700px) { .gideon-library { display:block; min-height:100dvh; } .gideon-library__rail { display:none; } .gideon-library__main { padding:18px 16px 36px; } .gideon-library__link { padding:16px; } }
      @media(max-width:700px) { .gideon-library-reader__pdf-frame { height:62vh; min-height:360px; } .gideon-library-reader__metadata { display:grid; grid-template-columns:minmax(0,1fr); } .gideon-library-reader__text { padding:16px; } }
    `}</style>
    {libraryStateMessage && <p className="gideon-library__notice" style={libraryPalette} role={stale ? "status" : "alert"} data-library-state={stale ? "stale" : error?.kind}>
      {libraryStateMessage}{(stale || !hasCurrentData) && <> <button type="button" onClick={retry}>{stale ? "Retry refresh" : "Retry"}</button></>}
    </p>}
    {!record && <div className="gideon-library gideon-library--index" style={libraryPalette}>
      <div className="gideon-library__main"><LibraryHome scope={scope} route={route} navigate={navigate} /></div>
    </div>}
    {record && activeItem.value && <div className="gideon-library gideon-library--reader" style={libraryPalette}><KnowledgeReader key={`${scope.cacheKey}\u0000${record.kind}:${record.id}`} scope={scope} item={activeItem.value} activeOwnerScope={activeOwnerScope} /></div>}
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

function recordTitle(record: LibraryRecordRef): string {
  return record.kind === "knowledge" ? "Knowledge item" : `${record.kind[0].toUpperCase()}${record.kind.slice(1)}`;
}
