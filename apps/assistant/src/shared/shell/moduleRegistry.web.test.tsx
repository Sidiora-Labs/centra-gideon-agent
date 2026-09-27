import { describe, expect, it } from "vitest";
import type { OwnerScope } from "../auth.web";
import { createShellRoute } from "./shellRoutes";
import { moduleDefinitions, moduleForRoute, resolveModuleRoute } from "./webModules.web";
import * as nativeModules from "./webModules.native";

const scope: OwnerScope = {
  runtimeOrigin: "https://gideon.example",
  ownerId: "owner-one",
  cacheKey: JSON.stringify(["https://gideon.example", "owner-one"]),
};

describe("assistant web module registry", () => {
  it("routes each published family entry to exactly one module owner", () => {
    const ownedRoutes = [
      ["tasks", createShellRoute("activity", {
        view: "detail", placement: { id: "tasks" }, record: { kind: "task", id: "task-one" },
      })],
      ["artifacts/editor", createShellRoute("apps", {
        view: "workspace", placement: { id: "artifacts/editor" }, record: { kind: "artifact", id: "artifact-one" },
      })],
      ["activity", createShellRoute("activity", { placement: { id: "activity" } })],
      ["apps", createShellRoute("apps", { placement: { id: "apps" } })],
      ["workflows", createShellRoute("activity", { view: "workspace", placement: { id: "workflows" } })],
      ["code", createShellRoute("apps", { view: "workspace", placement: { id: "projects" } })],
      ["studio", createShellRoute("apps", { view: "workspace", placement: { id: "capabilities/media/library" } })],
      ["ideas", createShellRoute("ideas")],
      ["knowledge", createShellRoute("apps", { view: "workspace", placement: { id: "knowledge" } })],
    ] as const;

    for (const [owner, route] of ownedRoutes) {
      const matches = moduleDefinitions.filter(definition => definition.matches(route));
      expect(matches.map(definition => definition.id), `${route.destination}/${route.placement?.id ?? "home"}`)
        .toEqual([owner]);
      expect(moduleForRoute(route)?.id).toBe(owner);
    }
  });

  it("keeps project planning inside Code ownership and reports unknown placements as unavailable", async () => {
    const planning = createShellRoute("apps", {
      view: "workspace",
      placement: { id: "projects/detail", subview: "/planning" },
      record: { kind: "project", id: "project-one" },
    });
    expect(moduleForRoute(planning)?.id).toBe("code");

    const unknown = createShellRoute("apps", { view: "workspace", placement: { id: "apps/unknown" } });
    expect(moduleForRoute(unknown)).toBeUndefined();
    expect(await resolveModuleRoute(scope, unknown)).toBe("unavailable");
    expect(moduleForRoute(createShellRoute("chat"))).toBeUndefined();
    expect(await resolveModuleRoute(scope, createShellRoute("chat"))).toBeUndefined();
  });

  it("does not register web modules in the native resolver", async () => {
    const route = createShellRoute("apps", { view: "workspace", placement: { id: "apps" } });
    expect(nativeModules.moduleDefinitions).toEqual([]);
    expect(await nativeModules.resolveModuleRoute(scope, route)).toBe("unavailable");
  });
});
