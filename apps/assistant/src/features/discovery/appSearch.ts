import type { OwnerScope } from "../../shared/auth.web";
import { GatewayError, gatewayJson } from "../../shared/transport.web";
import { DESTINATIONS, DESTINATION_GROUP_COUNTS, type DestinationCategory, type DestinationEntry } from "./destinations";

export type InstalledApp = Readonly<{
  name: string;
  displayName: string;
  description: string;
  tags: readonly string[];
  enabled: boolean;
  hasUI: boolean;
}>;

export type AppIndexResult =
  | Readonly<{ kind: "placement"; key: string; label: string; category: DestinationCategory; entry: DestinationEntry }>
  | Readonly<{ kind: "installed"; key: string; label: string; category: "Installed apps"; app: InstalledApp }>;

export type AppCategoryFilter = DestinationCategory | "Installed apps" | "All";
export type AppSourceFilter = "all" | "placements" | "installed";
export type AppAvailabilityFilter = "all" | "pending" | "ready" | "unavailable";
export type AppSearchFilters = Readonly<{
  query: string;
  category: AppCategoryFilter;
  source: AppSourceFilter;
  availability: AppAvailabilityFilter;
}>;

export const APP_CATEGORIES = Object.freeze(Object.keys(DESTINATION_GROUP_COUNTS) as DestinationCategory[]);
const PLACEMENT_KEYWORDS: Readonly<Record<string, readonly string[]>> = Object.freeze({
  "capabilities/communications/outbound": ["outbound", "email", "drafts", "inbox"],
});

function searchable(text: string): string {
  return text.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase().trim();
}

function matches(query: string, values: readonly string[]): boolean {
  const terms = searchable(query).split(/\s+/).filter(Boolean);
  const haystack = searchable(values.join(" "));
  return terms.every(term => haystack.includes(term));
}

export function searchApps(
  filters: AppSearchFilters,
  installed: readonly InstalledApp[],
  placements: readonly DestinationEntry[] = DESTINATIONS,
): AppIndexResult[] {
  const results: AppIndexResult[] = [];
  if (filters.source !== "installed" && filters.category !== "Installed apps") {
    for (const entry of placements) {
      if (filters.category !== "All" && entry.category !== filters.category) continue;
      if (filters.availability !== "all" && entry.availability.state !== filters.availability) continue;
      if (!matches(filters.query, [entry.label, entry.id, entry.category, ...(PLACEMENT_KEYWORDS[entry.id] ?? [])])) continue;
      results.push({ kind: "placement", key: `placement:${entry.id}`, label: entry.label, category: entry.category, entry });
    }
  }
  if (filters.source !== "placements" && (filters.category === "All" || filters.category === "Installed apps")
    && filters.availability === "all") {
    for (const app of installed) {
      if (!matches(filters.query, [app.displayName, app.name, app.description, ...app.tags])) continue;
      results.push({ kind: "installed", key: `installed:${app.name}`, label: app.displayName,
        category: "Installed apps", app });
    }
  }
  return results.sort((a, b) => a.label.localeCompare(b.label) || a.key.localeCompare(b.key));
}

export type InstalledAppsLoadError = "denied" | "unavailable" | "error";

export function classifyInstalledAppsError(error: unknown): InstalledAppsLoadError {
  if (error instanceof GatewayError && (error.status === 401 || error.status === 403)) return "denied";
  if (error instanceof GatewayError && error.status === 404) return "unavailable";
  return "error";
}

export async function readInstalledApps(scope: OwnerScope, signal?: AbortSignal): Promise<InstalledApp[]> {
  if (!scope.ownerId || scope.runtimeOrigin !== globalThis.location?.origin) {
    throw new GatewayError("The current owner cannot access this app collection", 403);
  }
  const response = await gatewayJson<{ apps: unknown }>("/api/apps", { signal });
  if (!response || !Array.isArray(response.apps)) throw new Error("The app collection response is invalid");
  return response.apps.map((value: unknown) => {
    if (!value || typeof value !== "object") throw new Error("The app collection contains an invalid entry");
    const app = value as Record<string, unknown>;
    if (typeof app.name !== "string" || !app.name.trim() || typeof app.displayName !== "string"
      || typeof app.description !== "string" || typeof app.enabled !== "boolean"
      || typeof app.hasUI !== "boolean" || !Array.isArray(app.tags)
      || !app.tags.every((tag: unknown) => typeof tag === "string")) {
      throw new Error("The app collection contains an invalid entry");
    }
    return Object.freeze({ name: app.name, displayName: app.displayName || app.name,
      description: app.description, tags: app.tags as string[], enabled: app.enabled, hasUI: app.hasUI });
  });
}
