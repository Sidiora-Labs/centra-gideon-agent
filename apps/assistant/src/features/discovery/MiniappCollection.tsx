import { useEffect, useRef, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import type { ShellReturnContext, ShellRoute } from "../../shared/shell/shellRoutes";
import { moduleForRoute } from "../../shared/shell/webModules.web";
import { useShellTheme } from "../../shared/shell/shellTheme";
import { MINIAPPS, miniappStateMessage, type Miniapp, type MiniappCheckState } from "./miniapps";

type MiniappCollectionProps = Readonly<{
  scope: OwnerScope;
  navigate: (route: ShellRoute) => void;
  returnTo: ShellReturnContext;
}>;

type CheckResult = Exclude<MiniappCheckState, "pending" | "checking" | "ready"> | "ready";

function canRenderFirstAction(miniapp: Miniapp): Promise<boolean> {
  if (miniapp.route.placement?.id !== miniapp.destinationId) return Promise.resolve(false);
  if (miniapp.destinationId.startsWith("capabilities/media/")
    || miniapp.destinationId.startsWith("capabilities/creative/")
    || miniapp.destinationId.startsWith("capabilities/music/")
    || miniapp.destinationId.startsWith("capabilities/experience/")) {
    return import("../studio/studioAdapters.web").then(({ hasNativeStudioView }) => hasNativeStudioView(miniapp.route));
  }
  return Promise.resolve(true);
}

async function checkFirstAction(scope: OwnerScope, miniapp: Miniapp): Promise<CheckResult> {
  const definition = moduleForRoute(miniapp.route);
  if (!definition) return "missing";
  try {
    const availability = await definition.resolve(scope, miniapp.route);
    if (availability !== "available") return availability;
    if (!await canRenderFirstAction(miniapp)) return "missing";
    await definition.load();
    return "ready";
  } catch {
    return "unavailable";
  }
}

export function MiniappCollection({ scope, navigate, returnTo }: MiniappCollectionProps) {
  const { palette } = useShellTheme();
  const [stored, setStored] = useState<{ scopeKey: string; states: Readonly<Record<string, MiniappCheckState>> }>(
    { scopeKey: scope.cacheKey, states: {} },
  );
  const currentScope = useRef(scope.cacheKey);
  currentScope.current = scope.cacheKey;
  const active = useRef(true);
  const attempt = useRef(0);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; attempt.current++; };
  }, [scope.cacheKey]);
  const states = stored.scopeKey === scope.cacheKey ? stored.states : {};
  const setState = (id: string, state: MiniappCheckState) => setStored(previous => ({
    scopeKey: scope.cacheKey,
    states: { ...(previous.scopeKey === scope.cacheKey ? previous.states : {}), [id]: state },
  }));

  async function open(miniapp: Miniapp) {
    const request = ++attempt.current;
    const scopeKey = scope.cacheKey;
    setState(miniapp.id, "checking");
    const result = await checkFirstAction(scope, miniapp);
    if (!active.current || currentScope.current !== scopeKey || attempt.current !== request) return;
    setState(miniapp.id, result);
    if (result === "ready") navigate({ ...miniapp.route, returnTo });
  }

  return <section aria-labelledby="miniapp-collection-title" style={{ margin: "20px 0 24px" }}>
    <div style={{ marginBottom: 12 }}>
      <h2 id="miniapp-collection-title" style={{ fontSize: 20, margin: "0 0 5px", color: palette.text }}>Gideon miniapps</h2>
      <p style={{ color: palette.muted, margin: 0 }}>Open a named workspace through its owning feature and account check.</p>
    </div>
    <ul aria-label="Gideon miniapps" style={{ listStyle: "none", padding: 0, margin: 0,
      display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fill, minmax(min(230px, 100%), 1fr))" }}>
      {MINIAPPS.map(miniapp => {
        const state = states[miniapp.id] ?? "pending";
        const definition = moduleForRoute(miniapp.route);
        const disabled = state === "checking" || !definition || state === "denied" || state === "missing";
        const label = state === "checking" ? "Checking workspace…"
          : state === "ready" ? miniapp.firstAction
          : state === "denied" ? "Access denied"
          : state === "missing" ? "Workspace unavailable"
          : definition ? `Check and ${miniapp.firstAction.toLocaleLowerCase()}` : "Workspace not available yet";
        return <li key={miniapp.id} style={{ minWidth: 0, border: `1px solid ${palette.line}`, borderRadius: 14,
          background: palette.card, color: palette.text, padding: 16, display: "grid", gap: 8, alignContent: "start" }}>
          <span style={{ color: palette.muted, fontSize: 12 }}>{miniapp.category}</span>
          <strong style={{ fontSize: 17 }}>{miniapp.name}</strong>
          <span style={{ color: palette.muted, fontSize: 14 }}>{miniapp.description}</span>
          <span style={{ fontSize: 13 }}><strong>First action:</strong> {miniapp.firstAction}</span>
          <span role="status" aria-live="polite" style={{ color: palette.muted, fontSize: 13 }}>
            {state === "pending" && !definition ? "Owning workspace is not registered yet."
              : miniappStateMessage(definition ? state : "missing", miniapp.name)}
          </span>
          <button type="button" disabled={disabled} onClick={() => void open(miniapp)}
            style={{ border: `1px solid ${palette.line}`, borderRadius: 10, background: palette.card,
              color: palette.text, font: "inherit", minHeight: 42, padding: "8px 12px", cursor: disabled ? "not-allowed" : "pointer" }}>
            {label}
          </button>
        </li>;
      })}
    </ul>
  </section>;
}

export default MiniappCollection;
