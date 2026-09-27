import { useSyncExternalStore } from "react";
import {
  createShellRoute,
  parseShellRoute,
  recoverShellRoute,
  serializeShellRoute,
  type ShellRecoveryRoute,
  type ShellRoute,
} from "./shellRoutes";

export type RouteAvailability = "available" | "missing" | "denied" | "unavailable";
export type AssistantRouteSnapshot =
  | { phase: "checking"; route: ShellRoute }
  | { phase: "ready"; route: ShellRoute }
  | { phase: "recovery"; route: ShellRecoveryRoute }
  | { phase: "unavailable"; route: ShellRoute; title: string; message: string };

export type AssistantRouteResolver = (route: ShellRoute) => RouteAvailability | Promise<RouteAvailability>;

export type AssistantRouteController = {
  getSnapshot: () => AssistantRouteSnapshot;
  subscribe: (listener: () => void) => () => void;
  navigate: (route: ShellRoute) => void;
  replace: (route: ShellRoute) => void;
  refresh: () => void;
  dispose: () => void;
};

function routeAtLocation(browser: Window): ShellRoute | ShellRecoveryRoute {
  const { pathname, search, hash } = browser.location;
  if ((pathname === "/assistant" || pathname === "/assistant/") && !search && !hash) {
    return createShellRoute("chat");
  }
  return parseShellRoute(browser.location.href, browser.location.origin);
}

function needsAuthority(route: ShellRoute): boolean {
  return !!(route.record || route.placement || route.sessionId || route.returnTo?.record
    || route.returnTo?.placement || route.returnTo?.sessionId);
}

export function createAssistantRouteController(options: {
  browser?: Window;
  resolveRoute?: AssistantRouteResolver;
} = {}): AssistantRouteController {
  const browser = options.browser ?? window;
  const listeners = new Set<() => void>();
  let generation = 0;
  let disposed = false;
  let snapshot: AssistantRouteSnapshot = { phase: "checking", route: createShellRoute("chat") };

  function commit(next: AssistantRouteSnapshot): void {
    if (disposed) return;
    snapshot = next;
    for (const listener of listeners) listener();
  }

  function refresh(): void {
    const current = ++generation;
    const parsed = routeAtLocation(browser);
    if (parsed.kind === "recovery") {
      commit({ phase: "recovery", route: parsed });
      return;
    }
    if (!options.resolveRoute) {
      commit(needsAuthority(parsed)
        ? { phase: "unavailable", route: parsed, title: "This item cannot be checked", message: "Try again when access checks are available." }
        : { phase: "ready", route: parsed });
      return;
    }
    commit({ phase: "checking", route: parsed });
    Promise.resolve().then(() => options.resolveRoute!(parsed)).then((availability) => {
      if (disposed || current !== generation) return;
      if (availability === "available") commit({ phase: "ready", route: parsed });
      else if (availability === "missing" || availability === "denied") {
        commit({ phase: "recovery", route: recoverShellRoute(availability) });
      } else commit({ phase: "unavailable", route: parsed, title: "This item cannot be checked", message: "Try again to check access." });
    }).catch(() => {
      if (!disposed && current === generation) {
        commit({ phase: "unavailable", route: parsed, title: "This item cannot be checked", message: "Try again to check access." });
      }
    });
  }

  function change(route: ShellRoute, replace: boolean): void {
    const href = serializeShellRoute(route);
    const parsed = parseShellRoute(href, browser.location.origin);
    if (parsed.kind === "recovery") throw new Error("Invalid assistant route");
    if (replace) browser.history.replaceState(null, "", href);
    else browser.history.pushState(null, "", href);
    refresh();
  }

  browser.addEventListener("popstate", refresh);
  refresh();
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    navigate: (route) => change(route, false),
    replace: (route) => change(route, true),
    refresh,
    dispose: () => { disposed = true; generation++; browser.removeEventListener("popstate", refresh); listeners.clear(); },
  };
}

export function useAssistantRouteState(controller: AssistantRouteController): AssistantRouteSnapshot {
  return useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
}
