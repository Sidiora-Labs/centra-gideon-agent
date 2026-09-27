import type { ShellReturnContext } from "../../shared/shell/shellRoutes";
import { MINIAPPS } from "./miniapps";

const STORAGE_PREFIX = "gideon.discovery.preferences.v1:";
const RECENT_LIMIT = 8;
const MINIAPP_IDS = new Set(MINIAPPS.map(miniapp => miniapp.id));

export type DiscoveryPreferences = Readonly<{
  pinnedIds: readonly string[];
  recentIds: readonly string[];
  lastInvokedId?: string;
}>;

export const EMPTY_DISCOVERY_PREFERENCES: DiscoveryPreferences = Object.freeze({
  pinnedIds: Object.freeze([]),
  recentIds: Object.freeze([]),
});

export function discoveryStorageKey(scopeKey: string): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(scopeKey)}`;
}

function validIds(value: unknown, limit = MINIAPP_IDS.size): string[] {
  if (!Array.isArray(value)) return [];
  const unique = new Set<string>();
  for (const id of value) {
    if (typeof id === "string" && MINIAPP_IDS.has(id)) unique.add(id);
    if (unique.size === limit) break;
  }
  return [...unique];
}

export function readDiscoveryPreferences(storage: Pick<Storage, "getItem"> | undefined,
  scopeKey: string): DiscoveryPreferences {
  if (!storage || !scopeKey) return EMPTY_DISCOVERY_PREFERENCES;
  try {
    const raw = storage.getItem(discoveryStorageKey(scopeKey));
    if (!raw) return EMPTY_DISCOVERY_PREFERENCES;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return EMPTY_DISCOVERY_PREFERENCES;
    const record = parsed as { version?: unknown; pinnedIds?: unknown; recentIds?: unknown; lastInvokedId?: unknown };
    if (record.version !== 1) return EMPTY_DISCOVERY_PREFERENCES;
    const pinnedIds = validIds(record.pinnedIds);
    const recentIds = validIds(record.recentIds, RECENT_LIMIT);
    const lastInvokedId = typeof record.lastInvokedId === "string" && MINIAPP_IDS.has(record.lastInvokedId)
      ? record.lastInvokedId : undefined;
    return { pinnedIds, recentIds, ...(lastInvokedId ? { lastInvokedId } : {}) };
  } catch {
    return EMPTY_DISCOVERY_PREFERENCES;
  }
}

export function writeDiscoveryPreferences(storage: Pick<Storage, "setItem"> | undefined,
  scopeKey: string, preferences: DiscoveryPreferences): void {
  if (!storage || !scopeKey) return;
  const pinnedIds = validIds(preferences.pinnedIds);
  const recentIds = validIds(preferences.recentIds, RECENT_LIMIT);
  const lastInvokedId = preferences.lastInvokedId && MINIAPP_IDS.has(preferences.lastInvokedId)
    ? preferences.lastInvokedId : undefined;
  const payload = {
    version: 1,
    pinnedIds,
    recentIds,
    ...(lastInvokedId ? { lastInvokedId } : {}),
  };
  try {
    storage.setItem(discoveryStorageKey(scopeKey), JSON.stringify(payload));
  } catch {
    // Discovery remains usable when browser storage is unavailable.
  }
}

export function togglePinned(preferences: DiscoveryPreferences, id: string): DiscoveryPreferences {
  if (!MINIAPP_IDS.has(id)) return preferences;
  const pinnedIds = preferences.pinnedIds.includes(id)
    ? preferences.pinnedIds.filter(value => value !== id)
    : [...preferences.pinnedIds, id];
  return { ...preferences, pinnedIds };
}

export function recordSuccessfulLaunch(preferences: DiscoveryPreferences, id: string): DiscoveryPreferences {
  if (!MINIAPP_IDS.has(id)) return preferences;
  return {
    ...preferences,
    recentIds: [id, ...preferences.recentIds.filter(value => value !== id)].slice(0, RECENT_LIMIT),
    lastInvokedId: id,
  };
}

export function miniappReturnContext(context: ShellReturnContext, id: string): ShellReturnContext {
  return MINIAPP_IDS.has(id) ? { ...context, selectionId: id } : context;
}
