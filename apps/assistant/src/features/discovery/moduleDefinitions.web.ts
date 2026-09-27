import type { ModuleDefinition } from "../../shared/shell/webModules.web";

export const discoveryModuleDefinitions: readonly ModuleDefinition[] = Object.freeze([
  {
    id: "apps",
    mode: "full",
    matches: route => route.destination === "apps" && !route.record
      && (!route.placement || route.placement.id === "apps"),
    resolve: async (scope, route) => {
      if (route.destination !== "apps" || route.record
        || (route.placement && route.placement.id !== "apps")) return "unavailable";
      if (!scope.ownerId || scope.runtimeOrigin !== globalThis.location?.origin) return "denied";
      return "available";
    },
    load: () => import("./AppsScreen"),
  },
]);
