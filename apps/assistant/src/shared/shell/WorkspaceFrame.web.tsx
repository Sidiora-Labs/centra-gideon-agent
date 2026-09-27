import type { CSSProperties, ReactNode } from "react";
import { useEffect, useRef } from "react";
import type { ParsedShellRoute } from "./shellRoutes";

export type WorkspaceFrameMode = "compact" | "full";

export type WorkspaceFrameState =
  | { kind: "ready" }
  | { kind: "loading"; message?: string }
  | { kind: "empty"; message: string }
  | { kind: "denied"; message: string }
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
  const routeKey = route.kind === "route"
    ? `${route.destination}:${route.view}:${route.record?.kind ?? ""}:${route.record?.id ?? ""}`
    : `recovery:${route.reason}`;

  useEffect(() => {
    headingRef.current?.focus();
  }, [routeKey]);

  const recovery = route.kind === "recovery";
  const message = recovery ? route.message : state.kind === "ready" ? undefined : state.message;
  const stateKind = recovery ? "recovery" : state.kind;

  return (
    <main aria-label={title} data-workspace-mode={mode} data-workspace-state={stateKind} style={workspaceFrameStyle(mode)}>
      <header style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 20px", flexWrap: "wrap" }}>
        {onBack && <button type="button" onClick={onBack} aria-label="Back">Back</button>}
        <h1 ref={headingRef} tabIndex={-1} style={{ flex: "1 1 auto", minWidth: 0, margin: 0 }}>
          {recovery ? route.title : title}
        </h1>
        {actions}
      </header>
      <div style={{ flex: "1 1 auto", minHeight: 0, overflow: "auto", padding: "0 20px 20px" }}>
        {message && <p role={stateKind === "error" || stateKind === "recovery" ? "alert" : "status"}>{message}</p>}
        {recovery && onGoToChat && <button type="button" onClick={onGoToChat}>{route.action.label}</button>}
        {!recovery && state.kind === "error" && state.onRetry && (
          <button type="button" onClick={state.onRetry}>Retry</button>
        )}
        {!recovery && state.kind === "ready" && children}
      </div>
    </main>
  );
}
