import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parseShellRoute, serializeShellRoute } from "../../shared/shell/shellRoutes";
import { DESTINATIONS, DESTINATION_GROUP_COUNTS, validateDestinations, type DestinationEntry } from "./destinations";

const checklist = readFileSync(new URL("../../../../../spec/gideon-assistant/destinations.md", import.meta.url), "utf8");
const checklistIds = [...checklist.matchAll(/^- \[ \] [^\n]*?\(`([^`]+)`\)/gm)].map(match => match[1]);

function replace(entry: DestinationEntry, change: Partial<DestinationEntry>): DestinationEntry {
  return { ...entry, ...change };
}

describe("assistant destination registry", () => {
  it("matches every public checklist ID and category count with an owner and pending verification", () => {
    expect(checklistIds).toHaveLength(214);
    expect(validateDestinations(DESTINATIONS, checklistIds)).toEqual([]);
    expect(new Set(DESTINATIONS.map(entry => entry.id)).size).toBe(214);
    for (const [category, count] of Object.entries(DESTINATION_GROUP_COUNTS)) {
      expect(DESTINATIONS.filter(entry => entry.category === category)).toHaveLength(count);
    }
    expect(DESTINATIONS.every(entry => entry.availability.state === "pending"
      && entry.availability.verification === "unverified")).toBe(true);
    expect(DESTINATIONS.find(entry => entry.id === "settings/security")).toMatchObject({
      category: "Settings", owner: "discovery", route: { destination: "apps", placement: { id: "settings/security" } },
    });
  });

  it("uses the shell placement and preserves record, subview, query and return context", () => {
    const entry = DESTINATIONS.find(item => item.id === "projects/detail")!;
    const route = {
      ...entry.route,
      record: { kind: "project", id: "project/7" },
      placement: { id: entry.id, subview: "/files/editor", query: { tab: "changes" } },
      returnTo: { destination: "chat" as const, placement: { id: "chat/session" }, selectionId: "message-2" },
    };
    expect(parseShellRoute(serializeShellRoute(route))).toEqual(route);
  });

  it("rejects duplicate, missing and extra IDs, wrong categories, missing fields and broken parents", () => {
    const first = DESTINATIONS[0];
    const second = DESTINATIONS[1];
    expect(validateDestinations([...DESTINATIONS, first], checklistIds)).toContain(`Duplicate ID: ${first.id}`);
    expect(validateDestinations(DESTINATIONS.slice(1), checklistIds)).toContain(`Missing ID: ${first.id}`);
    expect(validateDestinations([...DESTINATIONS, replace(first, { id: "extra" })], checklistIds)).toContain("Extra ID: extra");
    expect(validateDestinations([replace(first, { category: "Apps" }), ...DESTINATIONS.slice(1)], checklistIds))
      .toContain("Wrong Chat count: 7");
    expect(validateDestinations([replace(first, { owner: "" as DestinationEntry["owner"] }), ...DESTINATIONS.slice(1)], checklistIds))
      .toContain(`Missing owner: ${first.id}`);
    expect(validateDestinations([replace(first, { route: { ...first.route, placement: undefined } }), ...DESTINATIONS.slice(1)], checklistIds))
      .toContain(`Broken route: ${first.id}`);
    expect(validateDestinations([replace(first, { workspaceMode: "" as DestinationEntry["workspaceMode"] }), ...DESTINATIONS.slice(1)], checklistIds))
      .toContain(`Missing workspace mode: ${first.id}`);
    expect(validateDestinations([replace(first, { availability: undefined as unknown as DestinationEntry["availability"] }), ...DESTINATIONS.slice(1)], checklistIds))
      .toContain(`Missing availability or verification: ${first.id}`);
    expect(validateDestinations([first, replace(second, { parentId: "missing" }), ...DESTINATIONS.slice(2)], checklistIds))
      .toContain(`Broken parent: ${second.id}`);
  });
});
