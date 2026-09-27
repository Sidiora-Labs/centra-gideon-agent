import {
  createShellRoute,
  serializeShellRoute,
  type ShellRecord,
  type ShellRoute,
  type ShellReturnContext,
} from "../../shared/shell/shellRoutes";

export type LibraryRecordKind = "knowledge" | "collection" | "source" | "report" | "artifact";
export type LibraryRecordRef = Readonly<{ kind: LibraryRecordKind; id: string }>;

const LIBRARY_HOME = "knowledge";
const LIBRARY_ITEM = "knowledge/item";

export function libraryHomeRoute(origin?: ShellRoute): ShellRoute {
  return createShellRoute("apps", {
    view: "workspace",
    placement: { id: LIBRARY_HOME },
    ...(origin ? { returnTo: returnContext(origin) } : {}),
  });
}

export function libraryItemRoute(record: LibraryRecordRef, origin?: ShellRoute): ShellRoute {
  assertLibraryRecord(record);
  const returnTo = origin ? returnContext(origin, record.id) : undefined;
  return createShellRoute("apps", {
    view: "detail",
    record,
    placement: { id: LIBRARY_ITEM },
    ...(returnTo ? { returnTo } : {}),
  });
}

export function libraryRecordHref(record: LibraryRecordRef, origin?: ShellRoute): string {
  return serializeShellRoute(libraryItemRoute(record, origin));
}

export function libraryOriginRoute(route: ShellRoute): ShellRoute | undefined {
  if (route.destination === "apps" && route.placement?.id === LIBRARY_HOME) return route;
  if (route.destination === "apps" && route.placement?.id === LIBRARY_ITEM && route.returnTo?.destination === "apps") {
    return createShellRoute("apps", {
      view: "workspace",
      placement: { id: LIBRARY_HOME },
      returnTo: route.returnTo,
    });
  }
  return undefined;
}

export function parseLibraryRecord(route: ShellRoute): LibraryRecordRef | undefined {
  if (route.destination !== "apps" || route.placement?.id !== LIBRARY_ITEM || !route.record) return undefined;
  if (!isLibraryRecordKind(route.record.kind)) return undefined;
  return { kind: route.record.kind, id: route.record.id };
}

function returnContext(route: ShellRoute, selectedItemId?: string): ShellReturnContext {
  if (route.destination === "apps" && route.placement?.id === LIBRARY_HOME && route.returnTo) {
    return { ...route.returnTo, selectionId: selectedItemId ?? route.returnTo.selectionId };
  }
  return {
    destination: route.destination,
    record: route.record,
    placement: route.placement,
    sessionId: route.sessionId,
    selectionId: selectedItemId ?? route.record?.id,
  };
}

function isLibraryRecordKind(value: string): value is LibraryRecordKind {
  return value === "knowledge" || value === "collection" || value === "source" || value === "report" || value === "artifact";
}

function assertLibraryRecord(record: ShellRecord): asserts record is LibraryRecordRef {
  if (!isLibraryRecordKind(record.kind) || !record.id.trim()) throw new TypeError("A native Library record ID is required");
}
