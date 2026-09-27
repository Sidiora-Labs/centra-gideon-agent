import React from "react";
import type { ModuleDefinition, ModuleProps } from "../../shared/shell/webModules.web";
import { LibraryReadError, getLibraryRecord, listKnowledgeItems } from "./libraryApi";
import { LibraryWorkspace } from "./LibraryWorkspace.web";
import { parseLibraryRecord } from "./libraryRoutes";

function LibraryModule(props: ModuleProps) {
  return React.createElement(LibraryWorkspace, {
    route: props.route,
    scope: props.scope,
    navigate: props.navigate,
    onReturn: props.onReturn,
    returnTo: props.returnTo,
  });
}

function matchesLibraryRoute(route: ModuleProps["route"]): boolean {
  return route.destination === "apps" && (
    (route.placement?.id === "knowledge" && route.view === "workspace" && !route.record)
    || (route.placement?.id === "knowledge/item" && route.view === "detail" && parseLibraryRecord(route)?.kind === "knowledge")
  );
}

async function resolveLibraryRoute(scope: ModuleProps["scope"], route: ModuleProps["route"]): Promise<"available" | "missing" | "denied" | "unavailable"> {
  if (!matchesLibraryRoute(route)) return "missing";
  try {
    const record = parseLibraryRecord(route);
    if (record) await getLibraryRecord(scope, record);
    else await listKnowledgeItems(scope);
    return "available";
  } catch (error) {
    if (!(error instanceof LibraryReadError)) return "unavailable";
    if (error.kind === "forbidden") return "denied";
    if (error.kind === "missing") return "missing";
    return "unavailable";
  }
}

export const libraryModuleDefinitions: readonly ModuleDefinition[] = [
  {
    id: "library",
    mode: "full",
    matches: matchesLibraryRoute,
    resolve: resolveLibraryRoute,
    load: async () => ({ default: LibraryModule }),
  },
];
