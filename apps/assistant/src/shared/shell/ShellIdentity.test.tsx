import * as React from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { CONSOLE_HANDOFFS, ShellIdentity, shellIdentityView } from "./ShellIdentity";
import type { BootstrapState } from "../bootstrap.web";

describe("assistant shell identity", () => {
  it("uses the authenticated owner without displaying session credentials", () => {
    const state = {
      phase: "ready",
      status: { login_enabled: true, totp_required: false },
      owner: { user: "owner-one", username: "owner-one", session_ttl: "3600",
        login_enabled: true, credential_configured: true, totp_enabled: false,
        totp_required: false, lockout_threshold: 5, lockout_window: "300" },
      scope: { runtimeOrigin: "https://gideon.example", ownerId: "owner-one", cacheKey: "secret-cache-key" },
      error: "",
    } satisfies BootstrapState;
    expect(shellIdentityView(state)).toEqual({ name: "Gideon", status: "Signed in as owner-one", available: true });
    expect(JSON.stringify(shellIdentityView(state))).not.toContain("secret-cache-key");
    const markup = renderToStaticMarkup(<ShellIdentity state={state} onRefresh={() => {}} onSignOut={() => {}} />);
    expect(markup).toContain("Signed in as owner-one");
    expect(markup).toContain("Refresh Gideon session");
    expect(markup).toContain("Sign out of Gideon");
    expect(markup).not.toContain("secret-cache-key");
  });

  it("shows accurate checking, unavailable and sign-in states", () => {
    expect(shellIdentityView({ phase: "checking", status: null, owner: null, error: "" }).status)
      .toBe("Checking session…");
    expect(shellIdentityView({ phase: "unavailable", status: null, owner: null, error: "offline" }).status)
      .toBe("Connection unavailable");
    expect(shellIdentityView({ phase: "signed_out", status: null, owner: null, error: "" }).status)
      .toBe("Sign in required");
    const markup = renderToStaticMarkup(<ShellIdentity state={{ phase: "unavailable", status: null, owner: null, error: "offline" }}
      onRefresh={() => {}} onSignOut={() => {}} />);
    expect(markup).toContain("Connection unavailable");
    expect(markup).not.toContain("Sign out of Gideon");
  });

  it("points handoffs at existing same-origin console routes", () => {
    expect(CONSOLE_HANDOFFS.chat).toEqual({ label: "Chat", href: "/#/chat/new" });
    expect(CONSOLE_HANDOFFS.ideas.href).toBe("/#/capabilities/knowledge/ideas");
    expect(CONSOLE_HANDOFFS.goals.href).toBe("/#/capabilities/identity/goals");
    for (const handoff of Object.values(CONSOLE_HANDOFFS)) {
      expect(handoff.href).toMatch(/^\/#\/[a-z]+(?:\/[a-z]+)*$/);
    }
  });
});
