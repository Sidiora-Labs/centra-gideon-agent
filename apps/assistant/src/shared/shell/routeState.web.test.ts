import { execFile } from "node:child_process";
import { createServer } from "node:http";
import { resolve } from "node:path";
import { promisify } from "node:util";
import { buildSync } from "esbuild";
import { describe, expect, it } from "vitest";
import { createShellRoute, serializeShellRoute } from "./shellRoutes";

describe("assistant browser routes", () => {
  it("keeps deep links, history, typed handoffs and explicit recovery in a real browser", async () => {
    const deepLink = serializeShellRoute(createShellRoute("apps", {
      view: "workspace",
      record: { kind: "project", id: "project-8" },
      placement: { id: "projects/detail", subview: "/files/editor", query: { tab: "changes" } },
      sessionId: "session-4",
      returnTo: { destination: "chat", sessionId: "session-4", selectionId: "message-7", scrollY: 380 },
    }));
    const browserSource = `
      import { createAssistantRouteController } from "./src/shared/shell/routeState.web";
      import { createShellRoute, parseShellRoute } from "./src/shared/shell/shellRoutes";
      import { assistantHandoffHref, assistantReturnContextFromConsoleHash } from "../console/src/app/shell/assistantRouteBridge";

      const pause = () => new Promise(resolve => setTimeout(resolve, 30));
      async function until(check) {
        for (let n = 0; n < 90; n++) { if (check()) return true; await pause(); }
        return false;
      }
      async function main() {
        let failRetry = true;
        const resolveRoute = async route => {
          if (route.record?.id === "removed") return "missing";
          if (route.record?.id === "restricted") return "denied";
          if (route.record?.id === "retry" && failRetry) throw new Error("temporary network failure");
          if (route.record?.id === "slow") { await new Promise(resolve => setTimeout(resolve, 180)); return "missing"; }
          return "available";
        };
        let controller = createAssistantRouteController({ resolveRoute });
        const initial = await until(() => controller.getSnapshot().phase === "ready") &&
          controller.getSnapshot().route.destination === "apps" &&
          controller.getSnapshot().route.record.id === "project-8" &&
          controller.getSnapshot().route.placement.subview === "/files/editor" &&
          controller.getSnapshot().route.placement.query.tab === "changes" &&
          controller.getSnapshot().route.returnTo.sessionId === "session-4" &&
          controller.getSnapshot().route.returnTo.selectionId === "message-7";

        controller.navigate(createShellRoute("ideas", { returnTo: { destination: "chat", sessionId: "session-4" } }));
        const pushed = await until(() => controller.getSnapshot().phase === "ready" &&
          controller.getSnapshot().route.destination === "ideas") &&
          location.pathname === "/assistant/ideas" && location.search.includes("fromSession=session-4");
        history.back();
        const back = await until(() => controller.getSnapshot().phase === "ready" &&
          controller.getSnapshot().route.destination === "apps");
        history.forward();
        const forward = await until(() => controller.getSnapshot().phase === "ready" &&
          controller.getSnapshot().route.destination === "ideas");
        controller.dispose();
        controller = createAssistantRouteController({ resolveRoute });
        const refreshed = await until(() => controller.getSnapshot().phase === "ready") &&
          controller.getSnapshot().route.destination === "ideas";
        const withoutResolver = createAssistantRouteController();
        const protectedUnavailable = withoutResolver.getSnapshot().phase === "unavailable" &&
          withoutResolver.getSnapshot().route.destination === "ideas";
        withoutResolver.dispose();

        const handoff = assistantHandoffHref(createShellRoute("apps", {
          view: "detail", record: { kind: "app", id: "app-1" },
          placement: { id: "apps/detail", subview: "/overview", query: { tab: "metrics" } },
        }), "#/chat/session-9?draft=private&access_token=private");
        const handoffRoute = parseShellRoute(handoff, location.origin);
        const bridged = handoffRoute.kind === "route" &&
          handoffRoute.destination === "apps" && handoffRoute.record.id === "app-1" &&
          handoffRoute.placement.subview === "/overview" && handoffRoute.placement.query.tab === "metrics" &&
          handoffRoute.returnTo.destination === "chat" && handoffRoute.returnTo.sessionId === "session-9" &&
          !handoff.includes("draft") && !handoff.includes("token") &&
          assistantReturnContextFromConsoleHash("#/tasks/task-2?secret=private").record.id === "task-2" &&
          assistantReturnContextFromConsoleHash("#/capabilities/knowledge/ideas").destination === "ideas" &&
          assistantReturnContextFromConsoleHash("#/capabilities/identity/goals").destination === "goals";

        history.pushState(null, "", "/assistant/unknown?v=1");
        controller.refresh();
        const malformed = controller.getSnapshot().phase === "recovery" &&
          controller.getSnapshot().route.reason === "malformed" &&
          controller.getSnapshot().route.action.label === "Go to Chat";
        controller.navigate(createShellRoute("apps", { view: "detail", record: { kind: "app", id: "removed" } }));
        const missing = await until(() => controller.getSnapshot().phase === "recovery" &&
          controller.getSnapshot().route.reason === "missing");
        controller.navigate(createShellRoute("apps", { view: "detail", record: { kind: "app", id: "restricted" } }));
        const denied = await until(() => controller.getSnapshot().phase === "recovery" &&
          controller.getSnapshot().route.reason === "denied");
        controller.navigate(createShellRoute("apps", { view: "detail", record: { kind: "app", id: "retry" } }));
        const unavailable = await until(() => controller.getSnapshot().phase === "unavailable") &&
          controller.getSnapshot().route.record.id === "retry";
        failRetry = false;
        controller.refresh();
        const retried = await until(() => controller.getSnapshot().phase === "ready") &&
          controller.getSnapshot().route.record.id === "retry";

        controller.navigate(createShellRoute("apps", { view: "detail", record: { kind: "app", id: "slow" } }));
        controller.navigate(createShellRoute("chat"));
        const latest = await until(() => controller.getSnapshot().phase === "ready" &&
          controller.getSnapshot().route.destination === "chat");
        await new Promise(resolve => setTimeout(resolve, 220));
        const staleIgnored = latest && controller.getSnapshot().phase === "ready" &&
          controller.getSnapshot().route.destination === "chat";
        controller.dispose();
        document.getElementById("result").textContent = JSON.stringify({
          initial, pushed, back, forward, refreshed, protectedUnavailable, bridged, malformed, missing, denied, unavailable, retried, staleIgnored
        });
      }
      main().catch(error => document.getElementById("result").textContent = String(error));
    `;
    const bundle = buildSync({ stdin: { contents: browserSource, resolveDir: process.cwd(), loader: "js" },
      bundle: true, platform: "browser", format: "iife", write: false,
      alias: { react: resolve(process.cwd(), "node_modules/react"), "react-dom": resolve(process.cwd(), "node_modules/react-dom") },
    }).outputFiles[0].text;
    const server = createServer((_request, response) => {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end(`<html><body><pre id="result"></pre><script>${bundle}</script></body></html>`);
    });
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    try {
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("Browser server unavailable");
      const { stdout } = await promisify(execFile)("chromium", ["--headless", "--no-sandbox", "--disable-gpu",
        "--disable-dev-shm-usage", "--virtual-time-budget=10000", "--dump-dom",
        `http://127.0.0.1:${address.port}${deepLink}`], { timeout: 18000, maxBuffer: 4 * 1024 * 1024 });
      const match = stdout.match(/<pre id="result">([^<]+)<\/pre>/);
      expect(match, stdout.slice(-900)).not.toBeNull();
      expect(JSON.parse(match![1].replaceAll("&quot;", '"'))).toEqual({
        initial: true, pushed: true, back: true, forward: true, refreshed: true, protectedUnavailable: true, bridged: true,
        malformed: true, missing: true, denied: true, unavailable: true, retried: true, staleIgnored: true,
      });
    } finally {
      server.close();
    }
  }, 22000);
});
