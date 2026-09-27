import { describe, expect, it } from "vitest";
import {
  SHELL_DESTINATIONS,
  createShellRoute,
  parseShellRoute,
  recoverShellRoute,
  serializeShellRoute,
} from "./shellRoutes";
import { workspaceFrameStyle } from "./WorkspaceFrame.web";

describe("assistant routes", () => {
  it("keeps five stable labelled destinations", () => {
    expect(SHELL_DESTINATIONS).toEqual([
      { id: "chat", label: "Chat" },
      { id: "activity", label: "Activity" },
      { id: "ideas", label: "Ideas" },
      { id: "goals", label: "Goals" },
      { id: "apps", label: "Apps" },
    ]);
    for (const destination of SHELL_DESTINATIONS) {
      const route = createShellRoute(destination.id);
      expect(parseShellRoute(serializeShellRoute(route))).toMatchObject(route);
    }
  });

  it("round trips a workspace record, session and precise return context", () => {
    const route = createShellRoute("apps", {
      view: "workspace",
      record: { kind: "project", id: "project/with?reserved 日本語" },
      placement: { id: "projects/detail", subview: "/files/editor", query: { tab: "changes", selected: "file/1?x" } },
      sessionId: "session/with?reserved",
      returnTo: {
        destination: "chat",
        sessionId: "session/with?reserved",
        selectionId: "message-18",
        placement: { id: "chat/session", subview: "/thread", query: { tab: "history" } },
        scrollY: 2180,
      },
    });
    expect(parseShellRoute(serializeShellRoute(route))).toEqual(route);
  });

  it("rejects malformed, incomplete, external and unsupported links with a labelled recovery action", () => {
    for (const input of [
      "/assistant/unknown?v=1",
      "/assistant/activity?v=1&recordKind=task",
      "/assistant/apps?v=1&view=detail",
      "/assistant/chat?v=2",
      "/assistant/chat?v=1&v=1",
      "/assistant/chat?v=1&fromScroll=45",
      "/assistant/chat?v=1&session=%00bad",
      "/assistant/apps?v=1&subview=%2Feditor",
      "/assistant/apps?v=1&placement=apps&subview=%2F..%2Fsettings",
      "/assistant/apps?v=1&placement=apps&q.token=private",
      "/assistant/apps?v=1&placement=apps&q.tab=first&q.tab=second",
      "https://elsewhere.example/assistant/chat?v=1",
    ]) {
      expect(parseShellRoute(input)).toEqual(recoverShellRoute("malformed"));
    }
  });

  it("provides distinct recovery for removed and denied records", () => {
    expect(recoverShellRoute("missing")).toMatchObject({
      kind: "recovery", reason: "missing", action: { label: "Go to Chat" },
    });
    expect(recoverShellRoute("denied")).toMatchObject({
      kind: "recovery", reason: "denied", action: { label: "Go to Chat" },
    });
  });

  it("exposes centered reading and full workspace geometry", () => {
    expect(workspaceFrameStyle("compact")).toMatchObject({ maxWidth: 760, marginInline: "auto" });
    expect(workspaceFrameStyle("full")).toMatchObject({ width: "100%", height: "100%", maxWidth: "none" });
  });
});
