import type { ComponentType } from "react";
import type { OwnerScope } from "../auth.web";
import type { ShellRoute, ShellReturnContext } from "./shellRoutes";
import type { RouteAvailability } from "./routeState.web";
import type { WorkspaceFrameMode } from "./WorkspaceFrame.web";
import { createElement, useEffect, useState, type ReactNode } from "react";
import { GatewayError, gatewayJson } from "../transport.web";
import { serializeShellRoute } from "./shellRoutes";
import { useShellTheme } from "./shellTheme";
import { activityModuleDefinitions } from "../../features/activity/moduleDefinitions.web";
import { discoveryModuleDefinitions } from "../../features/discovery/moduleDefinitions.web";
import { workModuleDefinitions } from "../../features/work/moduleDefinitions.web";
import { codeModuleDefinition } from "../../features/code/moduleDefinitions.web";
import { studioModules } from "../../features/studio/moduleDefinitions.web";
import { personalModuleDefinitions } from "../../features/personal/moduleDefinitions.web";
import { libraryModuleDefinitions } from "../../features/library/moduleDefinitions.web";

export type ModuleProps = {
  route: ShellRoute;
  scope: OwnerScope;
  navigate: (route: ShellRoute) => void;
  returnTo?: ShellReturnContext;
  onReturn: () => void;
};

export type ModuleDefinition = {
  id: string;
  mode: WorkspaceFrameMode;
  matches: (route: ShellRoute) => boolean;
  resolve: (scope: OwnerScope, route: ShellRoute) => Promise<RouteAvailability>;
  load: () => Promise<{ default: ComponentType<ModuleProps> }>;
};

type ControlledThemeProvider = ComponentType<{ children: ReactNode; controlledMode?: "dark" | "light" }>;

type NativeRecord = { id: string; title: string; status: string } | { slug: string; name: string; kind: string };

function ownerSafe(scope: OwnerScope): boolean {
  return typeof window !== "undefined" && scope.ownerId.length > 0 &&
    scope.runtimeOrigin === window.location.origin;
}

async function resolveNativeRecord<T extends NativeRecord>(scope: OwnerScope, path: string, matches: (record: T) => boolean): Promise<RouteAvailability> {
  if (!ownerSafe(scope)) return "unavailable";
  try {
    const record = await gatewayJson<T>(path);
    return matches(record) ? "available" : "unavailable";
  } catch (error) {
    if (error instanceof GatewayError && error.status === 404) return "missing";
    if (error instanceof GatewayError && error.status === 403 && !error.authRequired) return "denied";
    return "unavailable";
  }
}

function taskId(route: ShellRoute): string | undefined {
  if (route.destination !== "activity" || (route.view !== "detail" && route.view !== "workspace") ||
    route.placement?.id !== "tasks" || route.record?.kind !== "task") return undefined;
  return route.record.id;
}

function artifactSlug(route: ShellRoute): string | undefined {
  if (route.destination !== "apps" || route.view !== "workspace" ||
    route.placement?.id !== "artifacts/editor" || route.record?.kind !== "artifact") return undefined;
  return route.record.id;
}

async function loadTaskModule(): Promise<{ default: ComponentType<ModuleProps> }> {
  const adapters = await import("./moduleAdapters.web");
  return { default: adapters.TaskModule };
}

async function loadArtifactModule(): Promise<{ default: ComponentType<ModuleProps> }> {
  const adapters = await import("./moduleAdapters.web");
  return { default: adapters.ArtifactModule };
}

const taskModuleDefinition: ModuleDefinition = Object.freeze({
  id: "tasks",
  mode: "full",
  matches: (route) => route.destination === "activity" &&
    (route.view === "detail" || route.view === "workspace") &&
    route.placement?.id === "tasks" && route.record?.kind === "task",
  resolve: (scope, route) => {
    const id = taskId(route);
    return id ? resolveNativeRecord(scope, `/api/tasks/${encodeURIComponent(id)}`,
      (task: { id: string; title: string; status: string }) => task.id === id &&
        typeof task.title === "string" && typeof task.status === "string") : Promise.resolve("unavailable");
  },
  load: loadTaskModule,
});

const artifactModuleDefinition: ModuleDefinition = Object.freeze({
  id: "artifacts/editor",
  mode: "full",
  matches: (route) => route.destination === "apps" && route.view === "workspace" &&
    route.placement?.id === "artifacts/editor" && route.record?.kind === "artifact",
  resolve: (scope, route) => {
    const slug = artifactSlug(route);
    return slug ? resolveNativeRecord(scope, `/api/artifacts/${encodeURIComponent(slug)}`,
      (artifact: { slug: string; name: string; kind: string }) => artifact.slug === slug &&
        typeof artifact.name === "string" && typeof artifact.kind === "string") : Promise.resolve("unavailable");
  },
  load: loadArtifactModule,
});

const registeredDefinitions: readonly ModuleDefinition[] = [
  taskModuleDefinition,
  artifactModuleDefinition,
  ...activityModuleDefinitions,
  ...discoveryModuleDefinitions,
  ...workModuleDefinitions,
  codeModuleDefinition,
  ...studioModules,
  ...personalModuleDefinitions,
  ...libraryModuleDefinitions,
];

export const moduleDefinitions: readonly ModuleDefinition[] = Object.freeze(registeredDefinitions);

export function moduleForRoute(route: ShellRoute): ModuleDefinition | undefined {
  const matches = moduleDefinitions.filter((definition) => definition.matches(route));
  if (matches.length > 1) {
    throw new Error(`Assistant route has multiple module owners: ${matches.map(({ id }) => id).join(", ")}`);
  }
  return matches[0];
}

export async function resolveModuleRoute(scope: OwnerScope, route: ShellRoute): Promise<RouteAvailability | undefined> {
  if (!route.placement) return undefined;
  const definition = moduleForRoute(route);
  return definition ? definition.resolve(scope, route) : "unavailable";
}

function routeKey(route: ShellRoute): string {
  return serializeShellRoute(route);
}

let trustedRuntime: Promise<void> | undefined;
function prepareTrustedRuntime(): Promise<void> {
  if (!trustedRuntime) {
    trustedRuntime = import("./trustedWebAssets.web").then(({ prepareTrustedWebRuntime }) => prepareTrustedWebRuntime())
      .catch((error: unknown) => {
        trustedRuntime = undefined;
        throw error;
      });
  }
  return trustedRuntime;
}

export function TrustedModuleContent({ definition, ...props }: ModuleProps & { definition: ModuleDefinition }) {
  const { mode } = useShellTheme();
  const key = `${definition.id}:${props.scope.cacheKey}:${routeKey(props.route)}`;
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{
    key: string;
    Component: ComponentType<ModuleProps>;
    ThemeProvider: ControlledThemeProvider;
  } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);

  useEffect(() => {
    let active = true;
    setFailure(null);
    void prepareTrustedRuntime().then(async () => {
      const [{ default: Component }, { ThemeProvider }] = await Promise.all([
        definition.load(),
        import("../../../../console/src/app/shell/theme"),
      ]);
      if (active) setLoaded({ key, Component, ThemeProvider });
    }).catch((error: unknown) => {
      if (active) setFailure({ key, message: error instanceof Error ? error.message : "The workspace could not be opened." });
    });
    return () => { active = false; };
  }, [definition, key, attempt]);

  if (failure?.key === key) return createElement("section", {
    role: "alert", "aria-label": "Workspace unavailable", className: "gideon-trusted-module-state",
  },
  createElement("p", null, "The trusted workspace could not be prepared. ", failure.message),
  createElement("button", { type: "button", onClick: () => setAttempt(value => value + 1) }, "Retry workspace"),
  createElement("button", { type: "button", onClick: props.onReturn }, "Return"));
  if (loaded?.key !== key) return createElement("p", {
    role: "status", "aria-live": "polite", className: "gideon-trusted-module-state",
  }, "Preparing trusted workspace…");
  return createElement(loaded.ThemeProvider, {
    controlledMode: mode,
    children: createElement("div", {
      className: `gideon-trusted-module ${mode}`,
      "data-gideon-module": definition.id,
    }, createElement(loaded.Component, { ...props, key })),
  });
}
