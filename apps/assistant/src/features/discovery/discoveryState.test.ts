import { describe, expect, it } from "vitest";
import {
  discoveryStorageKey, miniappReturnContext, readDiscoveryPreferences, recordSuccessfulLaunch,
  togglePinned, writeDiscoveryPreferences, type DiscoveryPreferences,
} from "./discoveryState";

function storage() {
  const values = new Map<string, string>();
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
}

describe("account scoped discovery preferences", () => {
  it("round trips pins and successful launch IDs without persisting labels", () => {
    const backing = storage();
    const origin = { destination: "apps" as const, placement: { id: "apps", query: { category: "Create" } },
      record: { kind: "folder", id: "folder-7" }, sessionId: "conversation-9" };
    const preferences = recordSuccessfulLaunch(togglePinned({ pinnedIds: [], recentIds: [] }, "slides"), "slides");

    writeDiscoveryPreferences(backing, "https://gideon.test:9443/owner-a", preferences);

    expect(readDiscoveryPreferences(backing, "https://gideon.test:9443/owner-a")).toEqual({
      pinnedIds: ["slides"], recentIds: ["slides"], lastInvokedId: "slides",
    });
    expect(backing.values.get(discoveryStorageKey("https://gideon.test:9443/owner-a"))).not.toContain("Slides");
    expect(readDiscoveryPreferences(backing, "https://gideon.test:9443/owner-b")).toEqual({ pinnedIds: [], recentIds: [] });
    expect(miniappReturnContext(origin, "slides")).toEqual({ ...origin, selectionId: "slides" });
  });

  it("keeps newest successful launches first, caps recents, and removes a pin only when toggled", () => {
    let preferences: DiscoveryPreferences = { pinnedIds: ["slides"], recentIds: [] };
    for (const id of ["research", "studio", "writer", "music", "worlds", "knowledge", "journal", "health", "people"]) {
      preferences = recordSuccessfulLaunch(preferences, id);
    }

    expect(preferences.recentIds).toEqual(["people", "health", "journal", "knowledge", "worlds", "music", "writer", "studio"]);
    expect(togglePinned(preferences, "slides").pinnedIds).toEqual([]);
    expect(recordSuccessfulLaunch(preferences, "not-a-miniapp")).toBe(preferences);
  });

  it("ignores corrupt, stale-version, and unknown preference records", () => {
    const backing = storage();
    const key = discoveryStorageKey("owner-a");
    backing.values.set(key, "{");
    expect(readDiscoveryPreferences(backing, "owner-a")).toEqual({ pinnedIds: [], recentIds: [] });
    backing.values.set(key, JSON.stringify({ version: 2, pinnedIds: ["slides"], recentIds: [] }));
    expect(readDiscoveryPreferences(backing, "owner-a")).toEqual({ pinnedIds: [], recentIds: [] });
    backing.values.set(key, JSON.stringify({ version: 1, pinnedIds: ["unknown", "slides", "slides"],
      recentIds: ["health", "unknown"], lastInvokedId: "unknown" }));
    expect(readDiscoveryPreferences(backing, "owner-a")).toEqual({
      pinnedIds: ["slides"], recentIds: ["health"],
    });
  });
});
