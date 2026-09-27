export const SHELL_DESTINATIONS = [
  { id: "chat", label: "Chat" },
  { id: "activity", label: "Activity" },
  { id: "ideas", label: "Ideas" },
  { id: "goals", label: "Goals" },
  { id: "apps", label: "Apps" },
] as const;

export type ShellDestination = (typeof SHELL_DESTINATIONS)[number]["id"];
export type ShellView = "list" | "detail" | "workspace";
export type ShellRecord = { kind: string; id: string };
export type ShellPlacement = {
  id: string;
  subview?: string;
  query?: Record<string, string>;
};

export type ShellReturnContext = {
  destination: ShellDestination;
  record?: ShellRecord;
  placement?: ShellPlacement;
  sessionId?: string;
  selectionId?: string;
  scrollY?: number;
};

export type ShellRoute = {
  kind: "route";
  destination: ShellDestination;
  view: ShellView;
  record?: ShellRecord;
  placement?: ShellPlacement;
  sessionId?: string;
  returnTo?: ShellReturnContext;
};

export type ShellRecoveryReason = "malformed" | "missing" | "denied";
export type ShellRecoveryRoute = {
  kind: "recovery";
  reason: ShellRecoveryReason;
  title: string;
  message: string;
  action: { label: "Go to Chat"; route: ShellRoute };
};

export type ParsedShellRoute = ShellRoute | ShellRecoveryRoute;

const BASE_PATH = "/assistant";
const ROUTE_VERSION = "1";
const ALLOWED_KEYS = new Set([
  "v", "view", "recordKind", "recordId", "session", "from", "fromKind",
  "fromId", "fromSession", "fromSelection", "fromScroll", "placement", "subview",
  "fromPlacement", "fromSubview",
]);
const QUERY_KEY_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,39}$/;
const SENSITIVE_KEY_PATTERN = /secret|token|password|credential|auth|code|key|draft|nonce|access|session|state/i;

function isDestination(value: string): value is ShellDestination {
  return SHELL_DESTINATIONS.some((destination) => destination.id === value);
}

function isView(value: string): value is ShellView {
  return value === "list" || value === "detail" || value === "workspace";
}

function validId(value: string | null | undefined): value is string {
  return typeof value === "string" && value.length <= 512 && value.trim().length > 0
    && !/[\u0000-\u001f\u007f]/.test(value);
}

function assertId(value: string, name: string): void {
  if (!validId(value)) throw new Error(`Invalid ${name}`);
}

function readId(params: URLSearchParams, key: string): string | undefined {
  const value = params.get(key);
  if (value === null) return undefined;
  if (!validId(value)) throw new Error(`Invalid ${key}`);
  return value;
}

function readRecord(params: URLSearchParams, kindKey: string, idKey: string): ShellRecord | undefined {
  const kind = readId(params, kindKey);
  const id = readId(params, idKey);
  if ((kind === undefined) !== (id === undefined)) throw new Error("Incomplete record");
  return kind && id ? { kind, id } : undefined;
}

function writeRecord(params: URLSearchParams, record: ShellRecord | undefined, kindKey: string, idKey: string): void {
  if (!record) return;
  assertId(record.kind, kindKey);
  assertId(record.id, idKey);
  params.set(kindKey, record.kind);
  params.set(idKey, record.id);
}

function validSubview(value: string): boolean {
  return value.length <= 512 && value.startsWith("/") && !value.startsWith("//")
    && !/[?#\u0000-\u001f\u007f]/.test(value)
    && !value.split("/").some((part) => part === "." || part === "..");
}

function validQueryKey(value: string): boolean {
  return QUERY_KEY_PATTERN.test(value) && !SENSITIVE_KEY_PATTERN.test(value);
}

function validQueryValue(value: string): boolean {
  return value.length <= 512 && !/[\u0000-\u001f\u007f]/.test(value);
}

function writePlacement(params: URLSearchParams, placement: ShellPlacement | undefined, prefix: "" | "from"): void {
  if (!placement) return;
  assertId(placement.id, "placement");
  params.set(prefix ? "fromPlacement" : "placement", placement.id);
  if (placement.subview !== undefined) {
    if (!validSubview(placement.subview)) throw new Error("Invalid subview");
    params.set(prefix ? "fromSubview" : "subview", placement.subview);
  }
  const entries = Object.entries(placement.query ?? {});
  if (entries.length > 16) throw new Error("Too many placement query fields");
  for (const [key, value] of entries) {
    if (!validQueryKey(key) || !validQueryValue(value)) throw new Error("Invalid placement query");
    params.set(`${prefix}q.${key}`, value);
  }
}

function readPlacement(params: URLSearchParams, prefix: "" | "from"): ShellPlacement | undefined {
  const placementId = readId(params, prefix ? "fromPlacement" : "placement");
  const subview = params.get(prefix ? "fromSubview" : "subview") ?? undefined;
  const queryPrefix = `${prefix}q.`;
  const queryEntries = [...params.entries()].filter(([key]) => key.startsWith(queryPrefix));
  if (!placementId && (subview !== undefined || queryEntries.length > 0)) throw new Error("Incomplete placement");
  if (subview !== undefined && !validSubview(subview)) throw new Error("Invalid subview");
  if (queryEntries.length > 16) throw new Error("Too many placement query fields");
  for (const [key, value] of queryEntries) {
    if (!validQueryKey(key.slice(queryPrefix.length)) || !validQueryValue(value)) throw new Error("Invalid placement query");
  }
  return placementId ? {
    id: placementId,
    subview,
    query: queryEntries.length ? Object.fromEntries(queryEntries.map(([key, value]) => [key.slice(queryPrefix.length), value])) : undefined,
  } : undefined;
}

export function createShellRoute(destination: ShellDestination, options: Partial<Omit<ShellRoute, "kind" | "destination">> = {}): ShellRoute {
  return { kind: "route", destination, view: options.view ?? "list", ...options };
}

export function recoverShellRoute(reason: ShellRecoveryReason): ShellRecoveryRoute {
  const content = {
    malformed: ["This link cannot be opened", "The assistant link is invalid or incomplete."],
    missing: ["This item is unavailable", "The requested item may have been removed."],
    denied: ["You cannot open this item", "Your account does not have access to the requested item."],
  }[reason];
  return {
    kind: "recovery",
    reason,
    title: content[0],
    message: content[1],
    action: { label: "Go to Chat", route: createShellRoute("chat") },
  };
}

export function serializeShellRoute(route: ShellRoute): string {
  if (!isDestination(route.destination) || !isView(route.view)) throw new Error("Invalid shell route");
  const params = new URLSearchParams({ v: ROUTE_VERSION });
  if (route.view !== "list") params.set("view", route.view);
  writeRecord(params, route.record, "recordKind", "recordId");
  writePlacement(params, route.placement, "");
  if (route.sessionId !== undefined) {
    assertId(route.sessionId, "session");
    params.set("session", route.sessionId);
  }
  if (route.returnTo) {
    if (!isDestination(route.returnTo.destination)) throw new Error("Invalid return destination");
    params.set("from", route.returnTo.destination);
    writeRecord(params, route.returnTo.record, "fromKind", "fromId");
    writePlacement(params, route.returnTo.placement, "from");
    if (route.returnTo.sessionId !== undefined) {
      assertId(route.returnTo.sessionId, "fromSession");
      params.set("fromSession", route.returnTo.sessionId);
    }
    if (route.returnTo.selectionId !== undefined) {
      assertId(route.returnTo.selectionId, "fromSelection");
      params.set("fromSelection", route.returnTo.selectionId);
    }
    if (route.returnTo.scrollY !== undefined) {
      if (!Number.isSafeInteger(route.returnTo.scrollY) || route.returnTo.scrollY < 0 || route.returnTo.scrollY > 10_000_000) {
        throw new Error("Invalid return scroll position");
      }
      params.set("fromScroll", String(route.returnTo.scrollY));
    }
  }
  const serialized = `${BASE_PATH}/${route.destination}?${params.toString()}`;
  if (serialized.length > 4096) throw new Error("Shell route is too long");
  return serialized;
}

export function parseShellRoute(input: string | URL, origin = "https://gideon.invalid"): ParsedShellRoute {
  try {
    const url = new URL(input, origin);
    if (url.origin !== new URL(origin).origin || url.hash || url.href.length > 4096) throw new Error("Invalid location");
    const parts = url.pathname.split("/");
    if (parts.length !== 3 || parts[0] !== "" || parts[1] !== "assistant" || !isDestination(parts[2])) {
      throw new Error("Invalid destination");
    }
    const params = url.searchParams;
    for (const key of params.keys()) {
      if (!(ALLOWED_KEYS.has(key) || key.startsWith("q.") || key.startsWith("fromq.")) || params.getAll(key).length !== 1) {
        throw new Error("Invalid parameters");
      }
    }
    if (params.get("v") !== ROUTE_VERSION) throw new Error("Unsupported route version");
    const view = params.get("view") ?? "list";
    if (!isView(view)) throw new Error("Invalid view");
    const record = readRecord(params, "recordKind", "recordId");
    const placement = readPlacement(params, "");
    if (view === "detail" && !record) throw new Error("Detail needs a record");
    const sessionId = readId(params, "session");
    const from = params.get("from");
    const hasFromFields = ["fromKind", "fromId", "fromSession", "fromSelection", "fromScroll", "fromPlacement", "fromSubview"].some((key) => params.has(key))
      || [...params.keys()].some((key) => key.startsWith("fromq."));
    if ((from === null && hasFromFields) || (from !== null && !isDestination(from))) throw new Error("Invalid return context");
    let returnTo: ShellReturnContext | undefined;
    if (from !== null && isDestination(from)) {
      const fromScroll = params.get("fromScroll");
      if (fromScroll !== null && (!/^(0|[1-9]\d*)$/.test(fromScroll) || Number(fromScroll) > 10_000_000)) {
        throw new Error("Invalid return scroll position");
      }
      returnTo = {
        destination: from,
        record: readRecord(params, "fromKind", "fromId"),
        placement: readPlacement(params, "from"),
        sessionId: readId(params, "fromSession"),
        selectionId: readId(params, "fromSelection"),
        scrollY: fromScroll === null ? undefined : Number(fromScroll),
      };
    }
    return { kind: "route", destination: parts[2], view, record, placement, sessionId, returnTo };
  } catch {
    return recoverShellRoute("malformed");
  }
}
