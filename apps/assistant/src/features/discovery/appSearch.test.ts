import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GatewayError } from "../../shared/transport.web";
import { DESTINATIONS } from "./destinations";
import AppsScreen from "./AppsScreen";
import { discoveryModuleDefinitions } from "./moduleDefinitions.web";
import {
  APP_CATEGORIES, classifyInstalledAppsError, readInstalledApps, searchApps,
  type AppSearchFilters, type InstalledApp,
} from "./appSearch";

const installed: readonly InstalledApp[] = [
  { name: "voice-notes", displayName: "Voice Notes", description: "Record and organize spoken notes",
    tags: ["audio", "capture"], enabled: true, hasUI: true },
  { name: "quiet-calendar", displayName: "Quiet Calendar", description: "Events and schedules",
    tags: ["planning"], enabled: false, hasUI: false },
];
const all: AppSearchFilters = { query: "", category: "All", source: "all", availability: "all" };

describe("Apps index", () => {
  it("includes every pending destination alongside actual installed-app entries without elevating readiness", () => {
    const results = searchApps(all, installed);
    expect(results).toHaveLength(DESTINATIONS.length + installed.length);
    expect(results.filter(result => result.kind === "placement")).toHaveLength(214);
    expect(results.filter(result => result.kind === "placement" && result.entry.availability.state !== "pending"))
      .toHaveLength(0);
    expect(results.filter(result => result.kind === "installed")).toHaveLength(2);
    expect(APP_CATEGORIES).toHaveLength(9);
  });

  it("finds labels, stable IDs, categories, installed descriptions and tags with combined terms", () => {
    expect(searchApps({ ...all, query: "account" }, installed).some(result => result.kind === "placement"
      && result.entry.id === "settings/account")).toBe(true);
    expect(searchApps({ ...all, query: "settings/security" }, installed).map(result => result.key))
      .toContain("placement:settings/security");
    expect(searchApps({ ...all, query: "voice audio" }, installed).map(result => result.key))
      .toEqual(["installed:voice-notes"]);
    expect(searchApps({ ...all, query: "spoken capture" }, installed).map(result => result.key))
      .toEqual(["installed:voice-notes"]);
    expect(searchApps({ ...all, query: "PERSONAL" }, installed).some(result => result.category === "Personal"))
      .toBe(true);
    for (const keyword of ["mail", "outbound", "email", "drafts", "inbox"]) {
      expect(searchApps({ ...all, query: keyword }, installed).some(result => result.kind === "placement"
        && result.entry.id === "capabilities/communications/outbound")).toBe(true);
    }
  });

  it("keeps pending matches visible through category, source and availability filters", () => {
    const settings = searchApps({ ...all, category: "Settings", availability: "pending" }, installed);
    expect(settings).toHaveLength(38);
    expect(settings.every(result => result.kind === "placement" && result.entry.availability.state === "pending"))
      .toBe(true);
    expect(searchApps({ ...all, source: "installed", query: "calendar" }, installed).map(result => result.key))
      .toEqual(["installed:quiet-calendar"]);
    expect(searchApps({ ...all, category: "Installed apps", availability: "pending" }, installed)).toEqual([]);
    expect(searchApps({ ...all, source: "placements", query: "calendar" }, installed)
      .every(result => result.kind === "placement")).toBe(true);
    expect(searchApps({ ...all, category: "Settings", query: "no-such-destination" }, installed)).toEqual([]);
  });

  it("distinguishes denied, unavailable and retryable installed-app failures", () => {
    expect(classifyInstalledAppsError(new GatewayError("Forbidden", 403))).toBe("denied");
    expect(classifyInstalledAppsError(new GatewayError("Missing", 404))).toBe("unavailable");
    expect(classifyInstalledAppsError(new Error("Network failed"))).toBe("error");
  });

  it("keeps the installed collection scoped to the current owner origin", async () => {
    await expect(readInstalledApps({ runtimeOrigin: "https://different.example", ownerId: "owner",
      cacheKey: "owner" })).rejects.toMatchObject({ status: 403 });
  });

  it("registers the Apps index only for its own route", () => {
    expect(discoveryModuleDefinitions).toHaveLength(1);
    const module = discoveryModuleDefinitions[0];
    const appsRoute = DESTINATIONS.find(entry => entry.id === "apps")!.route;
    const mailRoute = DESTINATIONS.find(entry => entry.id === "capabilities/communications/outbound")!.route;
    expect(module.id).toBe("apps");
    expect(module.matches(appsRoute)).toBe(true);
    expect(module.matches({ ...appsRoute, placement: undefined })).toBe(true);
    expect(module.matches(mailRoute)).toBe(false);
    expect(module.matches({ ...appsRoute, record: { kind: "app", id: "other" } })).toBe(false);
  });

  it("renders native keyboard controls, explicit pending status and screen reader feedback", () => {
    const route = DESTINATIONS.find(entry => entry.id === "apps")!.route;
    const html = renderToStaticMarkup(createElement(AppsScreen, { route,
      scope: { runtimeOrigin: "https://gideon.test", ownerId: "owner", cacheKey: "owner" },
      navigate: () => {}, onReturn: () => {},
    }));
    expect(html).toContain('role="search"');
    expect(html).toContain('type="search"');
    expect(html).toContain('aria-controls="apps-results"');
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain('aria-label="Apps search results"');
    expect(html).toContain("214 results; installed app results are incomplete");
    expect(html).toContain("Awaiting route verification");
    expect(html).not.toContain("Open Account</button>");
    expect(html).toContain("Loading installed apps. You can still browse destinations.");
  });
});
