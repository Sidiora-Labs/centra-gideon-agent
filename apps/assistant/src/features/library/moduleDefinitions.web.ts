import type { ModuleDefinition, ModuleProps } from "../../shared/shell/webModules.web";
import { LibraryReadError, getLibraryRecord, listKnowledgeItems } from "./libraryApi";
import { parseLibraryRecord } from "./libraryRoutes";

function matchesLibraryRoute(route: ModuleProps["route"]): boolean {
  if (route.destination !== "apps" || !route.placement || route.placement.subview !== undefined || route.placement.query !== undefined) return false;
  if (route.placement.id === "knowledge") return route.view === "workspace" && route.record === undefined;
  if (route.placement.id !== "knowledge/item" || route.view !== "detail" || route.record?.kind !== "knowledge") return false;
  return typeof route.record.id === "string" && route.record.id.trim().length > 0
    && !/[\u0000-\u001f\u007f]/.test(route.record.id);
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
    id: "knowledge",
    mode: "full",
    matches: matchesLibraryRoute,
    resolve: resolveLibraryRoute,
    load: async () => {
      const { LibraryWorkspace } = await import("./LibraryWorkspace.web");
      return { default: LibraryWorkspace };
    },
  },
];
