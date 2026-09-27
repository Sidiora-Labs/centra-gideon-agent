import type { OwnerScope } from "../../shared/auth.web";
import { GatewayError, gatewayJson, type GatewayMethod } from "../../shared/transport.web";
import type { LibraryRecordRef } from "./libraryRoutes";

export type KnowledgeItem = Readonly<{
  id: string;
  title: string;
  item_type?: string;
  kind?: string;
  provider?: string;
  source_id?: string | null;
  source_url?: string | null;
  created_at?: string;
  updated_at?: string;
  content?: string;
  content_truncated?: boolean;
  favorited?: boolean | number;
  read_state?: "unread" | "reading" | "read";
  status?: string;
}>;

export type KnowledgeList = Readonly<{
  items: readonly KnowledgeItem[];
  total: number;
}>;

export type LibraryCollection = Readonly<{
  id: string;
  name: string;
  kind: "manual" | "smart";
  query?: string;
  icon?: string;
  item_count?: number | null;
}>;

export type LibraryHome = Readonly<{
  recently_added: readonly KnowledgeItem[];
  continue_reading: readonly KnowledgeItem[];
  favorites: readonly KnowledgeItem[];
  collections: readonly LibraryCollection[];
}>;

export type CollectionResult = Readonly<{
  collection: LibraryCollection;
  items: readonly KnowledgeItem[];
}>;

export type LibraryReadErrorKind = "forbidden" | "unavailable" | "failed" | "missing";

export class LibraryReadError extends Error {
  constructor(readonly kind: LibraryReadErrorKind, message: string, readonly status?: number) {
    super(message);
    this.name = "LibraryReadError";
  }
}

function readError(error: unknown, detail: boolean): LibraryReadError {
  if (error instanceof LibraryReadError) return error;
  if (error instanceof GatewayError) {
    if (error.status === 401 || (error.status === 403 && error.authRequired)) {
      return new LibraryReadError("forbidden", "Your Gideon session no longer has access to this Library record.", error.status);
    }
    if (error.status === 403 && !error.authRequired) return new LibraryReadError("forbidden", "Your account cannot open this Library record.", 403);
    if (detail && error.status === 404) return new LibraryReadError("missing", "This Library record is unavailable.", 404);
    if (error.status === 502 || error.status === 503 || error.status === 504) {
      return new LibraryReadError("unavailable", "The knowledge service is temporarily unavailable.", error.status);
    }
    return new LibraryReadError("failed", error.message, error.status);
  }
  return new LibraryReadError("unavailable", "Gideon could not be reached. Check the connection and retry.");
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseItem(value: unknown): KnowledgeItem {
  if (!isObject(value) || typeof value.id !== "string" || !value.id || typeof value.title !== "string") {
    throw new LibraryReadError("failed", "Gideon returned an invalid knowledge record.");
  }
  return value as KnowledgeItem;
}

function assertScope(scope: OwnerScope): void {
  if (typeof window === "undefined" || scope.runtimeOrigin !== window.location.origin || !scope.ownerId) {
    throw new LibraryReadError("unavailable", "The signed-in Library account is not available on this address.");
  }
}

async function requestJson(scope: OwnerScope, path: string, options: { method?: GatewayMethod; body?: unknown; signal?: AbortSignal } = {}): Promise<unknown> {
  try {
    assertScope(scope);
    return await gatewayJson(path, options);
  } catch (error) {
    throw readError(error, false);
  }
}

function requireObject(value: unknown, message: string): Record<string, unknown> {
  if (!isObject(value)) throw new LibraryReadError("failed", message);
  return value;
}

function parseItems(value: unknown, message: string): KnowledgeItem[] {
  if (!Array.isArray(value)) throw new LibraryReadError("failed", message);
  return value.map(parseItem);
}

function parseCollection(value: unknown): LibraryCollection {
  if (!isObject(value) || typeof value.id !== "string" || typeof value.name !== "string" ||
      (value.kind !== "manual" && value.kind !== "smart")) {
    throw new LibraryReadError("failed", "Gideon returned an invalid collection.");
  }
  return value as LibraryCollection;
}

export async function listKnowledgeItems(scope: OwnerScope, signal?: AbortSignal): Promise<KnowledgeList> {
  try {
    assertScope(scope);
    const payload: unknown = await gatewayJson("/api/knowledge/items?limit=100", { signal });
    if (!isObject(payload) || !Array.isArray(payload.items)) throw new LibraryReadError("failed", "Gideon returned an invalid Library list.");
    const items = payload.items.map(parseItem);
    const total = typeof payload.total === "number" && Number.isFinite(payload.total) ? payload.total : items.length;
    return { items, total };
  } catch (error) {
    throw readError(error, false);
  }
}

export async function getLibraryHome(scope: OwnerScope, signal?: AbortSignal): Promise<LibraryHome> {
  const payload = requireObject(await requestJson(scope, "/api/knowledge/library-home", { signal }), "Gideon returned an invalid Library home.");
  return {
    recently_added: parseItems(payload.recently_added, "Gideon returned an invalid recent shelf."),
    continue_reading: parseItems(payload.continue_reading, "Gideon returned an invalid reading shelf."),
    favorites: parseItems(payload.favorites, "Gideon returned an invalid favorites shelf."),
    collections: Array.isArray(payload.collections) ? payload.collections.map(parseCollection) : [],
  };
}

export async function searchKnowledgeItems(
  scope: OwnerScope,
  filters: { query: string; type?: string; kind?: string; provider?: string; readState?: string; favorite?: boolean },
  signal?: AbortSignal,
): Promise<KnowledgeList> {
  const params = new URLSearchParams({ limit: "100", page: "1" });
  if (filters.query.trim()) params.set("q", filters.query.trim());
  if (filters.type) params.set("type", filters.type);
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.provider) params.set("provider", filters.provider);
  const items: KnowledgeItem[] = [];
  let total = 0;
  for (let page = 1; ; page += 1) {
    if (signal?.aborted) throw new DOMException("The Library search was cancelled.", "AbortError");
    params.set("page", String(page));
    const payload = requireObject(await requestJson(scope, `/api/knowledge/items?${params}`, { signal }), "Gideon returned an invalid Library search.");
    const pageItems = parseItems(payload.items, "Gideon returned an invalid Library search.");
    items.push(...pageItems);
    total = typeof payload.total === "number" && Number.isFinite(payload.total) ? payload.total : items.length;
    if (pageItems.length === 0 || items.length >= total) break;
  }
  const filtered = items.filter(item =>
    (!filters.readState || item.read_state === filters.readState) &&
    (filters.favorite === undefined || Boolean(item.favorited) === filters.favorite),
  );
  return { items: filtered, total: filtered.length };
}

export async function listLibraryCollections(scope: OwnerScope, signal?: AbortSignal): Promise<readonly LibraryCollection[]> {
  const payload = requireObject(await requestJson(scope, "/api/knowledge/collections", { signal }), "Gideon returned an invalid collection list.");
  return Array.isArray(payload.collections) ? payload.collections.map(parseCollection) : [];
}

export async function resolveLibraryCollection(scope: OwnerScope, id: string, signal?: AbortSignal): Promise<CollectionResult> {
  const payload = requireObject(await requestJson(scope, `/api/knowledge/collections/${encodeURIComponent(id)}/items`, { signal }), "Gideon returned an invalid collection.");
  return { collection: parseCollection(payload.collection), items: parseItems(payload.items, "Gideon returned invalid collection items.") };
}

export async function createLibraryCollection(
  scope: OwnerScope,
  input: { name: string; kind: "manual" | "smart"; query?: string },
  signal?: AbortSignal,
): Promise<LibraryCollection> {
  const payload = requireObject(await requestJson(scope, "/api/knowledge/collections", { method: "POST", body: input, signal }), "Gideon could not create that collection.");
  return parseCollection(payload.collection);
}

export async function addLibraryCollectionItem(scope: OwnerScope, collectionId: string, itemId: string, signal?: AbortSignal): Promise<void> {
  const payload = requireObject(await requestJson(scope, `/api/knowledge/collections/${encodeURIComponent(collectionId)}/items`, {
    method: "POST", body: { item_id: itemId }, signal,
  }), "Gideon could not add this item to the collection.");
  if (Array.isArray(payload.missing) && payload.missing.includes(itemId)) throw new LibraryReadError("missing", "This knowledge item is no longer available.");
}

export async function removeLibraryCollectionItem(scope: OwnerScope, collectionId: string, itemId: string, signal?: AbortSignal): Promise<void> {
  await requestJson(scope, `/api/knowledge/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`, { method: "DELETE", signal });
}

export async function setLibraryFavorite(scope: OwnerScope, itemId: string, favorited: boolean, signal?: AbortSignal): Promise<boolean> {
  const payload = requireObject(await requestJson(scope, `/api/knowledge/items/${encodeURIComponent(itemId)}/favorite`, {
    method: "POST", body: { value: favorited }, signal,
  }), "Gideon could not update this favorite.");
  if (typeof payload.favorited !== "boolean" && typeof payload.favorited !== "number") throw new LibraryReadError("failed", "Gideon did not confirm the favorite update.");
  return Boolean(payload.favorited);
}

export async function setLibraryReadState(scope: OwnerScope, itemId: string, state: "unread" | "reading" | "read", signal?: AbortSignal): Promise<KnowledgeItem["read_state"]> {
  const payload = requireObject(await requestJson(scope, `/api/knowledge/items/${encodeURIComponent(itemId)}/read-state`, {
    method: "POST", body: { state }, signal,
  }), "Gideon could not update reading progress.");
  if (payload.read_state !== "unread" && payload.read_state !== "reading" && payload.read_state !== "read") throw new LibraryReadError("failed", "Gideon did not confirm the reading update.");
  return payload.read_state;
}

export async function getKnowledgeItem(scope: OwnerScope, id: string, signal?: AbortSignal): Promise<KnowledgeItem> {
  if (!id.trim()) throw new TypeError("A native knowledge item ID is required");
  try {
    assertScope(scope);
    const payload: unknown = await gatewayJson(`/api/knowledge/items/${encodeURIComponent(id)}`, { signal });
    const item = parseItem(payload);
    if (item.id !== id) throw new LibraryReadError("failed", "Gideon returned a different knowledge record.");
    return item;
  } catch (error) {
    throw readError(error, true);
  }
}

export async function getLibraryRecord(scope: OwnerScope, record: LibraryRecordRef, signal?: AbortSignal): Promise<KnowledgeItem> {
  if (record.kind !== "knowledge") {
    throw new LibraryReadError("unavailable", `The ${record.kind} reader is not available yet.`);
  }
  return getKnowledgeItem(scope, record.id, signal);
}
