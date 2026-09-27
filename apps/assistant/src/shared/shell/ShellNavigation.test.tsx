import * as React from "react";
import { execFile } from "node:child_process";
import { createServer } from "node:http";
import { promisify } from "node:util";
import { renderToStaticMarkup } from "react-dom/server";
import { buildSync } from "esbuild";
import { describe, expect, it } from "vitest";
import { ShellNavigation, shellNavigationColumns } from "./ShellNavigation";
import { SHELL_DESTINATIONS } from "./shellRoutes";

const labels = SHELL_DESTINATIONS.map(({ label }) => label);

describe("assistant shell navigation", () => {
  it("renders all five labelled, focusable destinations with the current selection", () => {
    const markup = renderToStaticMarkup(<ShellNavigation selected="chat" onSelect={() => {}}
      availableWidth={540} fontScale={1} />);
    for (const label of labels) expect(markup).toContain(`aria-label="${label}"`);
    expect(markup.match(/role="tab"/g)).toHaveLength(5);
    expect(markup.match(/tabindex="0"/g)).toHaveLength(5);
    expect(markup.match(/aria-selected="true"/g)).toHaveLength(1);
    expect(markup).toContain("Assistant destinations");
    expect(renderToStaticMarkup(<ShellNavigation selected="apps" onSelect={() => {}}
      availableWidth={540} fontScale={1} />)).toMatch(/aria-label="Apps"[^>]*aria-selected="true"/);
  });

  it("keeps every label available across desktop, tablet, phone, zoom and long translations", () => {
    expect(shellNavigationColumns(540, 1)).toBe(5);
    expect(shellNavigationColumns(400, 1)).toBe(3);
    expect(shellNavigationColumns(276, 1)).toBe(3);
    expect(shellNavigationColumns(276, 2)).toBe(1);
    const translated = { activity: "Actividades prolongadas", apps: "Applications et intégrations" };
    expect(shellNavigationColumns(276, 1, translated)).toBe(1);
    const markup = renderToStaticMarkup(<ShellNavigation selected="apps" onSelect={() => {}}
      availableWidth={276} fontScale={2} labels={translated} />);
    expect(markup).toContain("Actividades prolongadas");
    expect(markup).toContain("Applications et intégrations");
    expect(markup).toContain("Chat");
    expect(markup).toContain("role=\"tab\"");
  });

  it("activates rendered tabs by click and keyboard, with narrow enlarged labels reachable", async () => {
    const browserSource = `
      import * as React from "react";
      import { createRoot } from "react-dom/client";
      import { ShellNavigation } from "./src/shared/shell/ShellNavigation";
      function Harness() {
        const [selected, setSelected] = React.useState("chat");
        return React.createElement(ShellNavigation, {
          selected, onSelect: setSelected, availableWidth: 276, fontScale: 2,
          labels: { activity: "Actividades prolongadas", apps: "Applications et intégrations" }
        });
      }
      createRoot(document.getElementById("root")).render(React.createElement(Harness));
      const pause = () => new Promise(resolve => setTimeout(resolve, 60));
      (async () => {
        await pause();
        const tabs = [...document.querySelectorAll('[role="tab"]')];
        const initial = tabs.length === 5 && tabs.every(tab => tab.tabIndex === 0) &&
          tabs[0].getAttribute("aria-selected") === "true";
        tabs[1].click();
        await pause();
        const clicked = tabs[1].getAttribute("aria-selected") === "true";
        tabs[2].focus();
        tabs[2].dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        tabs[2].dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", bubbles: true }));
        await pause();
        const keyboard = document.activeElement === tabs[2] &&
          tabs[2].getAttribute("aria-selected") === "true";
        const nav = document.querySelector('[role="tablist"]');
        const box = nav.getBoundingClientRect();
        const readable = tabs.every(tab => {
          const rect = tab.getBoundingClientRect();
          return rect.left >= box.left - 1 && rect.right <= box.right + 1 &&
            tab.textContent.trim().length > 0 && rect.height >= 62;
        });
        const scroll = nav.querySelector('[class*="r-overflow"]') || nav.firstElementChild;
        const reachable = scroll.scrollHeight > scroll.clientHeight;
        scroll.scrollTop = scroll.scrollHeight;
        document.getElementById("result").textContent = JSON.stringify({
          initial, clicked, keyboard, readable, reachable,
          labels: tabs.map(tab => tab.textContent.trim())
        });
      })().catch(error => document.getElementById("result").textContent = String(error));
    `;
    const bundle = buildSync({ stdin: { contents: browserSource, resolveDir: process.cwd(), loader: "js" },
      bundle: true, platform: "browser", format: "iife", write: false,
      alias: { "react-native": "react-native-web" } }).outputFiles[0].text;
    const html = `<html><body><div id="root" style="width:276px"></div><pre id="result"></pre><script>${bundle}</script></body></html>`;
    const server = createServer((_request, response) => {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end(html);
    });
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    try {
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("Browser server unavailable");
      const { stdout } = await promisify(execFile)("chromium", ["--headless", "--no-sandbox", "--disable-gpu",
        "--disable-dev-shm-usage", "--virtual-time-budget=3000", "--dump-dom", `http://127.0.0.1:${address.port}/`],
      { timeout: 10000, maxBuffer: 4 * 1024 * 1024 });
      const match = stdout.match(/<pre id="result">([^<]+)<\/pre>/);
      expect(match, stdout.slice(-500)).not.toBeNull();
      expect(JSON.parse(match![1].replaceAll("&quot;", '"'))).toEqual({
        initial: true, clicked: true, keyboard: true, readable: true, reachable: true,
        labels: ["Chat", "Actividades prolongadas", "Ideas", "Goals", "Applications et intégrations"],
      });
    } finally {
      server.close();
    }
  });

});
