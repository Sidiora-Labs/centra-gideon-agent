import * as React from "react";
import { mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:http";
import { renderToStaticMarkup } from "react-dom/server";
import { buildSync } from "esbuild";
import { describe, expect, it } from "vitest";
import { startBrowserHarness } from "../../../test-support/browserHarness";
import { ShellNavigation, shellNavigationColumns } from "./ShellNavigation";
import { SHELL_DESTINATIONS } from "./shellRoutes";

const labels = SHELL_DESTINATIONS.map(({ label }) => label);
type TabGeometry = { left: number; right: number; top: number; bottom: number; width: number; height: number };
type PhoneGeometry = { viewport: number; navWidth: number; boxes: TabGeometry[]; oneRow: boolean;
  contained: boolean; touchSized: boolean; selected: string | null; labels: string[] };

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
    expect(shellNavigationColumns(316, 1)).toBe(5);
    expect(shellNavigationColumns(346, 1)).toBe(5);
    expect(shellNavigationColumns(400, 1)).toBe(5);
    expect(shellNavigationColumns(276, 1)).toBe(3);
    expect(shellNavigationColumns(276, 2)).toBe(2);
    const translated = { activity: "Actividades prolongadas", apps: "Applications et intégrations" };
    expect(shellNavigationColumns(276, 1, translated)).toBe(1);
    const markup = renderToStaticMarkup(<ShellNavigation selected="apps" onSelect={() => {}}
      availableWidth={276} fontScale={2} labels={translated} />);
    expect(markup).toContain("Actividades prolongadas");
    expect(markup).toContain("Applications et intégrations");
    expect(markup).toContain("Chat");
    expect(markup).toContain("role=\"tab\"");
  });

  it("keeps phone tabs in one touch-sized row and preserves keyboard and enlarged-label access", async () => {
    const browserSource = `
      import * as React from "react";
      import { createRoot } from "react-dom/client";
      import { Bell, Lightbulb, MessageCircle, PanelsTopLeft, Shapes } from "lucide-react";
      import { ShellNavigation } from "./src/shared/shell/ShellNavigation";
      const icons = { chat: MessageCircle, activity: PanelsTopLeft, ideas: Lightbulb, goals: Bell, apps: Shapes };
      function Harness() {
        const [selected, setSelected] = React.useState("chat");
        const query = new URLSearchParams(location.search);
        const scale = Number(query.get("scale") || "1");
        const labels = query.get("long") ? {
          activity: "Actividades prolongadas",
          apps: "Applications et intégrations"
        } : undefined;
        return React.createElement(ShellNavigation, {
          selected, onSelect: setSelected, availableWidth: Math.max(1, innerWidth - 44),
          fontScale: scale, labels, icons
        });
      }
      createRoot(document.getElementById("root")).render(React.createElement(Harness));
    `;
    const browserBundle = buildSync({ stdin: { contents: browserSource, resolveDir: process.cwd(), loader: "js" },
      outfile: join(tmpdir(), "gideon-shell-navigation-browser.js"),
      bundle: true, platform: "browser", format: "iife", write: false,
      resolveExtensions: [".web.tsx", ".web.ts", ".web.js", ".tsx", ".ts", ".jsx", ".js", ".json"],
      alias: { "react-native": "react-native-web" } });
    const bundle = browserBundle.outputFiles.find(file => file.path.endsWith(".js"))!.text;
    const styles = browserBundle.outputFiles.find(file => file.path.endsWith(".css"))?.text ?? "";
    const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
      <style>html,body{margin:0;min-height:100%;font-family:Arial,sans-serif}#root{width:calc(100% - 44px);margin:0 22px}</style>
      <style>${styles}</style></head><body><div id="root"></div><script>${bundle}</script></body></html>`;
    const server = createServer((_request, response) => {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end(html);
    });
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    const browser = await startBrowserHarness({ windowSize: { width: 360, height: 900 } });
    try {
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("Browser server unavailable");
      const url = `http://127.0.0.1:${address.port}/`;
      const normal = async (width: number) => {
        await browser.command("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: true });
        await browser.navigate(url);
        await browser.waitFor('document.querySelectorAll("[role=tab]").length === 5', `${width}px shell tabs`);
        return browser.evaluate<PhoneGeometry>(`(() => {
          const nav = document.querySelector('[role="tablist"]');
          const tabs = [...nav.querySelectorAll('[role="tab"]')];
          const navBox = nav.getBoundingClientRect();
          const boxes = tabs.map(tab => { const box = tab.getBoundingClientRect(); return {
            left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height
          }; });
          return { viewport: innerWidth, navWidth: navBox.width, boxes,
            oneRow: boxes.every(box => Math.abs(box.top - boxes[0].top) < 1),
            contained: boxes.every(box => box.left >= navBox.left && box.right <= navBox.right),
            touchSized: boxes.every(box => box.width >= 44 && box.height >= 44),
            selected: tabs[0].getAttribute('aria-selected'), labels: tabs.map(tab => tab.textContent.trim()) };
        })()`);
      };
      const phone360 = await normal(360);
      expect(phone360.viewport).toBe(360);
      expect(phone360.oneRow).toBe(true);
      expect(phone360.contained).toBe(true);
      expect(phone360.touchSized).toBe(true);
      expect(phone360.boxes).toHaveLength(5);
      expect(phone360.selected).toBe("true");
      expect(phone360.boxes.map(box => Math.round(box.width))).toEqual([61, 61, 61, 61, 61]);

      await browser.evaluate('document.querySelectorAll("[role=tab]")[2].focus()');
      await browser.command("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Enter", code: "Enter" });
      await browser.command("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter" });
      await browser.waitFor('document.querySelectorAll("[role=tab]")[2].getAttribute("aria-selected") === "true"', "keyboard destination selection");
      const touchTarget = await browser.evaluate<{ x: number; y: number }>(`(() => {
        const box = document.querySelectorAll('[role="tab"]')[3].getBoundingClientRect();
        return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
      })()`);
      await browser.command("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ ...touchTarget, id: 1 }] });
      await browser.command("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await browser.waitFor('document.querySelectorAll("[role=tab]")[3].getAttribute("aria-selected") === "true"', "touch destination selection");

      const evidenceDirectory = process.env.GIDEON_TEST_EVIDENCE_DIR ?? join(tmpdir(), "gideon-shell-navigation");
      await mkdir(evidenceDirectory, { recursive: true });
      const screenshot = async (name: string) => {
        const result = await browser.command("Page.captureScreenshot", { format: "png", fromSurface: true }) as { data: string };
        await writeFile(join(evidenceDirectory, name), Buffer.from(result.data, "base64"));
      };
      await screenshot("shell-navigation-360.png");

      const phone390 = await normal(390);
      expect(phone390.viewport).toBe(390);
      expect(phone390.oneRow).toBe(true);
      expect(phone390.contained).toBe(true);
      expect(phone390.touchSized).toBe(true);
      expect(phone390.boxes).toHaveLength(5);

      await browser.command("Emulation.setDeviceMetricsOverride", { width: 360, height: 900, deviceScaleFactor: 1, mobile: true });
      await browser.navigate(`${url}?scale=2&long=1`);
      await browser.waitFor('document.querySelectorAll("[role=tab]").length === 5', "enlarged translated shell tabs");
      const enlarged = await browser.evaluate<{ navWidth: number; rows: number; contained: boolean;
        touchSized: boolean; scrollable: boolean; labels: string[] }>(`(() => {
        const nav = document.querySelector('[role="tablist"]');
        const scroll = nav.firstElementChild;
        const tabs = [...nav.querySelectorAll('[role="tab"]')];
        const box = nav.getBoundingClientRect();
        const rects = tabs.map(tab => { const rect = tab.getBoundingClientRect(); return {
          left: rect.left, right: rect.right, top: rect.top, height: rect.height, width: rect.width
        }; });
        return { navWidth: box.width, rows: new Set(rects.map(rect => Math.round(rect.top))).size,
          contained: rects.every(rect => rect.left >= box.left && rect.right <= box.right),
          touchSized: rects.every(rect => rect.width >= 44 && rect.height >= 44),
          scrollable: scroll.scrollHeight > scroll.clientHeight,
          labels: tabs.map(tab => tab.textContent.trim()) };
      })()`);
      expect(enlarged.rows).toBeGreaterThan(1);
      expect(enlarged.contained).toBe(true);
      expect(enlarged.touchSized).toBe(true);
      expect(enlarged.scrollable).toBe(true);
      expect(enlarged.labels).toEqual(["Chat", "Actividades prolongadas", "Ideas", "Goals", "Applications et intégrations"]);
      await screenshot("shell-navigation-360-enlarged.png");
    } finally {
      await browser.close();
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  }, 15000);

});
