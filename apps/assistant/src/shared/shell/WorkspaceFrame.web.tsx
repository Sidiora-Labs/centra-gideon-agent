import type { CSSProperties, ReactNode } from "react";
import * as React from "react";
import { useLayoutEffect, useRef } from "react";
import { serializeShellRoute, type ParsedShellRoute } from "./shellRoutes";
import "./workspaceFrame.web.css";

export type WorkspaceFrameMode = "compact" | "full";

export type WorkspaceFrameState =
  | { kind: "ready" }
  | { kind: "loading"; message?: string }
  | { kind: "empty"; message: string; action?: ReactNode }
  | { kind: "denied"; message: string; action?: ReactNode }
  | { kind: "error"; message: string; onRetry?: () => void };

export type WorkspaceFrameProps = {
  route: ParsedShellRoute;
  mode: WorkspaceFrameMode;
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  state?: WorkspaceFrameState;
  onBack?: () => void;
  onGoToChat?: () => void;
};

export function workspaceFrameStyle(mode: WorkspaceFrameMode): CSSProperties {
  return {
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    flex: "1 1 auto",
    minWidth: 0,
    minHeight: 0,
    height: "100%",
    overflow: "hidden",
    width: "100%",
    maxWidth: mode === "compact" ? 760 : "none",
    marginInline: mode === "compact" ? "auto" : undefined,
  };
}

export function WorkspaceFrame({
  route,
  mode,
  title,
  children,
  actions,
  state = { kind: "ready" },
  onBack,
  onGoToChat,
}: WorkspaceFrameProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const previousRouteKey = useRef<string | null>(null);
  const scrollPositions = useRef(new Map<string, number>());
  const routeKey = route.kind === "route"
    ? serializeShellRoute(route)
    : `recovery:${route.reason}`;

  useLayoutEffect(() => {
    const previousKey = previousRouteKey.current;
    const body = bodyRef.current;
    if (previousKey !== routeKey && body) body.scrollTop = scrollPositions.current.get(routeKey) ?? 0;
    if (previousKey !== routeKey) headingRef.current?.focus({ preventScroll: true });
    previousRouteKey.current = routeKey;
  }, [routeKey]);

  const recovery = route.kind === "recovery";
  const stateKind = recovery ? "recovery" : state.kind;
  const message = recovery ? route.message : state.kind === "ready" ? undefined : state.message;

  return (
    <main aria-label={title} aria-busy={!recovery && state.kind === "loading"}
      className="gideon-workspace-frame" data-workspace-mode={mode} data-workspace-state={stateKind}
      style={workspaceFrameStyle(mode)}>
      <header className="gideon-workspace-header">
        <div className="gideon-workspace-heading">
          {onBack && <button type="button" className="gideon-workspace-back" onClick={onBack} aria-label="Back">Back</button>}
          <h1 ref={headingRef} tabIndex={-1}>
          {recovery ? route.title : title}
          </h1>
        </div>
        {actions && <div className="gideon-workspace-actions">{actions}</div>}
      </header>
      <div ref={bodyRef} className="gideon-workspace-scroll" data-workspace-scroll
        onScroll={event => scrollPositions.current.set(routeKey, event.currentTarget.scrollTop)}>
        {message && <p className="gideon-workspace-message" role={stateKind === "error" || stateKind === "recovery" || stateKind === "denied" ? "alert" : "status"}>{message}</p>}
        {!recovery && state.kind === "loading" && <div className="gideon-workspace-loading" aria-hidden="true" />}
        {recovery && onGoToChat && <button className="gideon-workspace-recovery" type="button" onClick={onGoToChat}>{route.action.label}</button>}
        {!recovery && state.kind === "error" && state.onRetry && (
          <button className="gideon-workspace-recovery" type="button" onClick={state.onRetry}>Retry</button>
        )}
        {!recovery && state.kind === "denied" && onGoToChat && (
          <button className="gideon-workspace-recovery" type="button" onClick={onGoToChat}>Go to Chat</button>
        )}
        {!recovery && (state.kind === "empty" || state.kind === "denied") && state.action}
        {!recovery && (state.kind === "ready" || state.kind === "empty") && children}
      </div>
    </main>
  );
}
