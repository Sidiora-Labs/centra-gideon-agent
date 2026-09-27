import type { OwnerScope } from "../../shared/auth.web";
import { GatewayError, gatewayJson } from "../../shared/transport.web";
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
}>;

export type KnowledgeList = Readonly<{
  items: readonly KnowledgeItem[];
  total: number;
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
