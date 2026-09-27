import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { describe, expect, it } from "vitest";
import { Card, Button } from "../../ui";
import { ShellIdentity } from "./ShellIdentity";
import { resolveShellTheme, ShellThemeControls, ShellThemeProvider, shellPalettes } from "./shellTheme.web";
import type { BootstrapState } from "../bootstrap.web";

const ready: BootstrapState = {
  phase: "ready", status: { login_enabled: true, totp_required: false },
  owner: { user: "owner-one", username: "owner-one", session_ttl: "3600",
    login_enabled: true, credential_configured: true, totp_enabled: false,
    totp_required: false, lockout_threshold: 5, lockout_window: "300" },
  scope: { runtimeOrigin: "https://gideon.example", ownerId: "owner-one", cacheKey: "private" }, error: "",
};

function rendered(preference: "light" | "dark" | "system") {
  return renderToStaticMarkup(
    <ShellThemeProvider initialPreference={preference}>
      <ShellIdentity state={ready} onRefresh={() => {}} onSignOut={() => {}} />
      <Card><Button onPress={() => {}}>Continue</Button></Card>
      <ShellThemeControls />
    </ShellThemeProvider>,
  );
}

function contrast(foreground: string, background: string) {
  const luminance = (hex: string) => {
    const channels = hex.match(/[a-f\d]{2}/gi)!.map((channel) => parseInt(channel, 16) / 255)
      .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

describe("assistant theme", () => {
  it("renders the retained identity, card and controls in scoped light and dark modes", () => {
    const light = rendered("light");
    const dark = rendered("dark");
    expect(light).toContain('data-gideon-assistant=""');
    expect(light).toContain('data-theme="light"');
    expect(dark).toContain('data-theme="dark"');
    expect(light).toContain("Signed in as owner-one");
    expect(dark).toContain("Signed in as owner-one");
    expect(light).toContain("Continue");
    expect(dark).toContain("Continue");
    expect(light).not.toBe(dark);
    expect(light).not.toContain("private");
    expect(dark).not.toContain("private");
  });

  it("provides labelled, selected, touch-sized light, dark and system controls", () => {
    for (const preference of ["light", "dark", "system"] as const) {
      const markup = rendered(preference);
      for (const choice of ["Light", "Dark", "System"]) expect(markup).toContain(`${choice} theme`);
      expect(markup).toContain(`aria-label="${preference[0].toUpperCase()}${preference.slice(1)} theme"`);
      expect(markup.match(/aria-checked="true"/g)).toHaveLength(1);
    }
  });

  it("follows system changes only while system preference is selected and keeps readable palettes", () => {
    expect(resolveShellTheme("system", "light")).toBe("light");
    expect(resolveShellTheme("system", "dark")).toBe("dark");
    expect(resolveShellTheme("light", "dark")).toBe("light");
    expect(resolveShellTheme("dark", "light")).toBe("dark");
    for (const palette of Object.values(shellPalettes)) {
      expect(contrast(palette.text, palette.card)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(palette.muted, palette.canvas)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("switches theme, restores overlay focus and retains scoped controls in a real browser", async () => {
    const directory = await mkdtemp(join(tmpdir(), "gideon-theme-"));
    const fixture = `
      import * as React from "react";
      import { createRoot } from "react-dom/client";
      import { ShellThemeProvider, ShellThemeControls, useShellTheme } from "./shellTheme.web";
      import { ShellOverlayRoot } from "./ShellOverlayRoot.web";
      function Probe() {
        const { mode, direction } = useShellTheme();
        const [open, setOpen] = React.useState(false);
        return <div><ShellThemeControls /><output id="state">{mode}:{direction}</output>
          <button id="open-overlay" onClick={() => setOpen(true)}>Open details</button>
          {open && <ShellOverlayRoot onClose={() => setOpen(false)}>
            <button id="close-overlay" onClick={() => setOpen(false)}>Close details</button>
          </ShellOverlayRoot>}
        </div>;
      }
      createRoot(document.getElementById("root")).render(<ShellThemeProvider><Probe /></ShellThemeProvider>);
    `;
    const sourceDirectory = dirname(fileURLToPath(import.meta.url));
    await build({ stdin: { contents: fixture, resolveDir: sourceDirectory, sourcefile: "theme-fixture.tsx", loader: "tsx" },
      bundle: true, platform: "browser", format: "iife", outfile: join(directory, "app.js"),
      alias: { "react-native": "react-native-web" },
      define: { "process.env.NODE_ENV": '"production"', __DEV__: "false" }, logLevel: "silent" });
    await writeFile(join(directory, "index.html"), '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body><div id="root"></div><button id="outside" style="transition:opacity 4s">Outside</button><script src="/app.js"></script></body></html>');
    const server = createServer(async (request, response) => {
      const name = request.url === "/app.js" ? "app.js" : request.url === "/app.css" ? "app.css" : "index.html";
      response.setHeader("content-type", name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html");
      response.end(await readFile(join(directory, name)));
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("Fixture server did not bind");
    const profile = join(directory, "chrome");
    const browser = spawn(process.env.CHROMIUM_BIN || "chromium", ["--headless=new", "--no-sandbox", "--disable-gpu",
      "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
    { stdio: "ignore" });
    let socket: WebSocket | undefined;
    try {
      const started = Date.now();
      let port = 0;
      while (!port && Date.now() - started < 10000) {
        try { port = Number((await readFile(join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0]); }
        catch { await new Promise((resolve) => setTimeout(resolve, 100)); }
      }
      if (!port) throw new Error("Chromium debugging endpoint did not start");
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json() as { type: string; webSocketDebuggerUrl: string }[];
      const target = targets.find((entry) => entry.type === "page");
      if (!target) throw new Error("Chromium page target was unavailable");
      socket = new WebSocket(target.webSocketDebuggerUrl);
      await new Promise<void>((resolve, reject) => { socket!.addEventListener("open", () => resolve(), { once: true }); socket!.addEventListener("error", reject, { once: true }); });
      let nextId = 0;
      const pending = new Map<number, { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void }>();
      socket.addEventListener("message", (event) => {
        const message = JSON.parse(String(event.data)) as { id?: number; result?: Record<string, unknown>; error?: { message: string } };
        if (!message.id) return;
        const entry = pending.get(message.id);
        if (!entry) return;
        pending.delete(message.id);
        if (message.error) entry.reject(new Error(message.error.message)); else entry.resolve(message.result || {});
      });
      const send = (method: string, params: Record<string, unknown> = {}) => new Promise<Record<string, unknown>>((resolve, reject) => {
        const id = ++nextId;
        pending.set(id, { resolve, reject });
        socket!.send(JSON.stringify({ id, method, params }));
      });
      const evaluate = async <T,>(expression: string): Promise<T> => {
        const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }) as {
          result?: { value?: T }; exceptionDetails?: { text: string };
        };
        if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
        return result.result?.value as T;
      };
      const until = async (expression: string) => {
        const start = Date.now();
        while (Date.now() - start < 5000) {
          if (await evaluate<boolean>(expression)) return;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        throw new Error(`Browser condition did not become true: ${expression}`);
      };
      await send("Emulation.setDeviceMetricsOverride", { width: 360, height: 640, deviceScaleFactor: 1, mobile: true });
      await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
      await send("Page.navigate", { url: `http://127.0.0.1:${address.port}/` });
      await until('document.querySelector("[data-gideon-assistant]")?.getAttribute("data-theme") === "light"');
      expect(await evaluate<string>('document.querySelector("[aria-checked=true]")?.getAttribute("aria-label")')).toBe("System theme");
      await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }] });
      await until('document.querySelector("[data-gideon-assistant]")?.getAttribute("data-theme") === "dark"');
      await evaluate('document.querySelector("[aria-label=\"Light theme\"]").click()');
      await until('document.querySelector("[data-gideon-assistant]")?.getAttribute("data-theme") === "light"');
      await evaluate('document.querySelector("[aria-label=\"Dark theme\"]").click()');
      await until('document.querySelector("[data-gideon-assistant]")?.getAttribute("data-theme") === "dark"');
      await evaluate('document.querySelector("[aria-label=\"System theme\"]").click()');
      await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" },
        { name: "prefers-reduced-motion", value: "reduce" }] });
      await until('document.querySelector("[data-gideon-assistant]")?.getAttribute("data-theme") === "light"');
      const motion = await evaluate<[string, string]>(`(() => { const inside = document.querySelector('[data-gideon-assistant] button');
        inside.style.transition = 'opacity 4s'; return [getComputedStyle(inside).transitionDuration,
        getComputedStyle(document.getElementById('outside')).transitionDuration]; })()`);
      expect(parseFloat(motion[0])).toBeLessThan(0.01);
      expect(motion[1]).toBe("4s");
      await evaluate('document.documentElement.dir = "rtl"');
      await until('document.querySelector("[data-gideon-assistant]")?.getAttribute("data-direction") === "rtl"');
      await evaluate('document.body.style.zoom = "200%"');
      const controlsFit = await evaluate<boolean>(`[...document.querySelectorAll('[aria-label$=" theme"]')]
        .every((control) => { const box = control.getBoundingClientRect();
          return box.width >= 44 && box.left >= 0 && box.right <= innerWidth + 1; })`);
      expect(controlsFit).toBe(true);
      await evaluate('document.getElementById("open-overlay").focus(); document.getElementById("open-overlay").click()');
      await until('document.activeElement?.id === "close-overlay"');
      expect(await evaluate<string>('document.querySelector("[data-gideon-assistant-overlay]")?.getAttribute("dir")')).toBe("rtl");
      await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await until('!document.querySelector("[data-gideon-assistant-overlay]") && document.activeElement?.id === "open-overlay"');
    } finally {
      socket?.close();
      browser.kill();
      await new Promise<void>((resolve) => server.close(() => resolve()));
      await rm(directory, { recursive: true, force: true });
    }
  }, 45000);
});
