import { describe, expect, it } from "vitest";
import type { OwnerScope } from "../auth.web";
import { createShellRoute } from "./shellRoutes";
import {
  moduleDefinitions,
  moduleForRoute,
  resolveModuleRoute,
} from "./webModules.web";
import * as nativeModules from "./webModules.native";

const scope: OwnerScope = {
  runtimeOrigin: "https://gideon.example",
  ownerId: "owner-one",
  cacheKey: JSON.stringify(["https://gideon.example", "owner-one"]),
};

describe("trusted assistant module registration", () => {
  it("keeps the published task and artifact editor placements as unique full workspaces", () => {
    const taskRoute = createShellRoute("activity", {
      view: "detail", placement: { id: "tasks" }, record: { kind: "task", id: "task-one" },
    });
    const artifactRoute = createShellRoute("apps", {
      view: "workspace", placement: { id: "artifacts/editor" }, record: { kind: "artifact", id: "artifact-one" },
    });
    for (const [route, id] of [[taskRoute, "tasks"], [artifactRoute, "artifacts/editor"]] as const) {
      const owners = moduleDefinitions.filter((definition) => definition.matches(route));
      expect(owners.map((definition) => definition.id)).toEqual([id]);
      expect(owners[0]?.mode).toBe("full");
      expect(moduleForRoute(route)?.id).toBe(id);
    }
    expect(moduleForRoute(createShellRoute("apps", {
      view: "workspace", placement: { id: "tasks" }, record: { kind: "task", id: "task-one" },
    }))).toBeUndefined();
    expect(moduleForRoute(createShellRoute("activity", {
      view: "workspace", placement: { id: "artifacts/editor" }, record: { kind: "artifact", id: "artifact-one" },
    }))).toBeUndefined();
  });

  it("keeps unregistered or structurally incomplete placements unavailable without loading a module", async () => {
    const unknown = createShellRoute("apps", { view: "workspace", placement: { id: "apps/detail" } });
    const invalidTask = createShellRoute("activity", {
      view: "workspace", placement: { id: "tasks" }, record: { kind: "artifact", id: "task-one" },
    });
    const misplacedTask = createShellRoute("apps", {
      view: "workspace", placement: { id: "tasks" }, record: { kind: "task", id: "task-one" },
    });
    const misplacedArtifact = createShellRoute("activity", {
      view: "workspace", placement: { id: "artifacts/editor" }, record: { kind: "artifact", id: "artifact-one" },
    });
    expect(await resolveModuleRoute(scope, createShellRoute("chat"))).toBeUndefined();
    expect(await resolveModuleRoute(scope, unknown)).toBe("unavailable");
    expect(await resolveModuleRoute(scope, invalidTask)).toBe("unavailable");
    expect(moduleForRoute(misplacedTask)).toBeUndefined();
    expect(moduleForRoute(misplacedArtifact)).toBeUndefined();
    expect(await resolveModuleRoute(scope, misplacedTask)).toBe("unavailable");
    expect(await resolveModuleRoute(scope, misplacedArtifact)).toBe("unavailable");
  });

  it("keeps native placement resolution unavailable and free of web module imports", async () => {
    const route = createShellRoute("apps", {
      view: "workspace", placement: { id: "artifacts/editor" }, record: { kind: "artifact", id: "artifact-one" },
    });
    expect(nativeModules.moduleDefinitions).toEqual([]);
    expect(await nativeModules.resolveModuleRoute(scope, route)).toBe("unavailable");
  });
});
