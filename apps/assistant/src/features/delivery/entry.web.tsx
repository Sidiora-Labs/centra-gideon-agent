import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import { GatewayError, gatewayJson } from "../../shared/transport.web";
import {
  createAssistantRouteController,
  type AssistantRouteController,
  type AssistantRouteSnapshot,
  type RouteAvailability,
} from "../../shared/shell/routeState.web";
import { resolveModuleRoute } from "../../shared/shell/webModules";
import type { ShellRoute } from "../../shared/shell/shellRoutes";

export { assistantConsoleReturnHref as consoleReturnHref } from "../../../../console/src/app/shell/assistantRouteBridge";

export type OwnedSession = Readonly<{
  key: string;
  title: string;
  total: number;
}>;

const checkingRoute: AssistantRouteSnapshot = { phase: "checking", route: {
  kind: "route", destination: "chat", view: "list",
} };

export function targetSessionId(route: ShellRoute): string | null | undefined {
  if (route.placement) return undefined;
  if (route.record && (route.destination !== "chat" ||
    (route.record.kind !== "session" && route.record.kind !== "chat-session" &&
      route.record.kind !== "chat_session"))) return undefined;
  if (route.sessionId && route.destination !== "chat") return undefined;
  if (route.record && route.sessionId && route.record.id !== route.sessionId) return undefined;
  return route.sessionId ?? route.record?.id ?? null;
}

export function createOwnedRouteResolver(scope: OwnerScope, sessions: Map<string, OwnedSession>) {
  return async (route: ShellRoute): Promise<RouteAvailability> => {
    const moduleAvailability = await resolveModuleRoute(scope, route);
    if (moduleAvailability !== undefined) return moduleAvailability;
    const sessionId = targetSessionId(route);
    if (sessionId === undefined) return "unavailable";
    if (sessionId === null) return "available";
    if (scope.runtimeOrigin !== window.location.origin || !scope.ownerId) return "unavailable";
    try {
      const detail = await gatewayJson<unknown>(`/api/chat/sessions/${encodeURIComponent(sessionId)}`);
      if (!detail || typeof detail !== "object" || !("key" in detail) ||
        typeof detail.key !== "string" || !("title" in detail) ||
        typeof detail.title !== "string" || !("total" in detail) ||
        typeof detail.total !== "number" ||
        detail.key !== sessionId) return "unavailable";
      sessions.set(sessionId, { key: detail.key, title: detail.title, total: detail.total });
      return "available";
    } catch (error) {
      if (error instanceof GatewayError && error.status === 404) return "missing";
      if (error instanceof GatewayError && error.status === 403 && !error.authRequired) return "denied";
      return "unavailable";
    }
  };
}

export function useAssistantEntry(scope: OwnerScope) {
  const [entry, setEntry] = useState<{ scopeKey: string; controller: AssistantRouteController;
    sessions: Map<string, OwnedSession> } | null>(null);
  useEffect(() => {
    const sessions = new Map<string, OwnedSession>();
    const controller = createAssistantRouteController({ resolveRoute: createOwnedRouteResolver(scope, sessions) });
    setEntry({ scopeKey: scope.cacheKey, controller, sessions });
    return () => controller.dispose();
  }, [scope.cacheKey]);
  const active = entry?.scopeKey === scope.cacheKey ? entry : null;
  const subscribe = useCallback((listener: () => void) => active?.controller.subscribe(listener) ?? (() => {}), [active]);
  const getSnapshot = useCallback(() => active?.controller.getSnapshot() ?? checkingRoute, [active]);
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return {
    snapshot,
    navigate: (route: ShellRoute) => active?.controller.navigate(route),
    refresh: () => active?.controller.refresh(),
    session: snapshot.phase === "ready" && snapshot.route.destination === "chat"
      ? active?.sessions.get(targetSessionId(snapshot.route) ?? "") : undefined,
  };
}
