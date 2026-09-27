import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { once } from "node:events";
import { JSDOM } from "jsdom";
import { afterEach, describe, expect, it } from "vitest";
import { prepareTrustedWebRuntime } from "./trustedWebAssets.web";
import { buildTrustedWebAssets } from "../../../tooling/buildTrustedWebAssets.mjs";

const assistantRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const consoleRoot = path.resolve(assistantRoot, "../console");
const assetBuilder = await import(new URL("../../../tooling/buildTrustedWebAssets.mjs", import.meta.url).href);
type BuiltAsset = { path: string; bytes: number; sha256: string };
const outputs: string[] = [];

afterEach(async () => {
  await Promise.all(outputs.splice(0).map((output) => rm(output, { recursive: true, force: true })));
});

describe("trusted web assets", () => {
  it("builds the console CSS and real Monaco worker bundles required by assistant modules", async () => {
    const outputDirectory = await mkdtemp(path.join(os.tmpdir(), "gideon-trusted-assets-"));
    outputs.push(outputDirectory);
    const assets: BuiltAsset[] = await assetBuilder.buildTrustedWebAssets({ outputDirectory, assistantDirectory: assistantRoot, consoleDirectory: consoleRoot });
    const css = await readFile(path.join(outputDirectory, "gideon-console.css"), "utf8");

    expect(css).toContain("--color-canvas");
    expect(css).toContain(".gideon-trusted-module .h-full");
    expect(css).toContain("gideon-workspace");
    expect(css).toContain(".gideon-trusted-module-dialog-root .h-full");
    expect(css).toContain('.gideon-trusted-module[data-gideon-module="artifacts/editor"]');
    expect(css).toContain("url(./fonts/inter.woff2)");
    expect(assets.filter((asset) => asset.path.startsWith("workers/")).map((asset) => asset.path)).toEqual([
      "workers/gideon-monaco-editor.worker.js",
      "workers/gideon-monaco-json.worker.js",
      "workers/gideon-monaco-css.worker.js",
      "workers/gideon-monaco-html.worker.js",
      "workers/gideon-monaco-typescript.worker.js",
    ]);
    for (const asset of assets.filter((entry) => entry.path.startsWith("workers/"))) {
      expect((await stat(path.join(outputDirectory, asset.path))).size).toBeGreaterThan(1000);
      expect(asset.sha256).toMatch(/^[a-f0-9]{64}$/);
    }
    expect(JSON.parse(await readFile(path.join(outputDirectory, "manifest.json"), "utf8")).assets).toHaveLength(11);

    const dialogSource = await readFile(path.join(consoleRoot, "src/shared/ui/dialog/DialogShell.tsx"), "utf8");
    expect(dialogSource).toContain("data-gideon-dialog-portal");
    let stylesheetAvailable = false;
    let stylesheetRequests = 0;
    const server = createServer(async (request, response) => {
      const pathname = new URL(request.url ?? "/", `http://${request.headers.host}`).pathname;
      const relativePath = pathname.replace(/^\/assistant\/assets\//, "");
      if (pathname === "/assistant/assets/gideon-console.css") {
        stylesheetRequests += 1;
        if (!stylesheetAvailable) {
          response.writeHead(404).end();
          return;
        }
      }
      const filePath = path.resolve(outputDirectory, relativePath);
      if (!filePath.startsWith(`${path.resolve(outputDirectory)}${path.sep}`)) {
        response.writeHead(400).end();
        return;
      }
      try {
        const content = await readFile(filePath);
        const type = filePath.endsWith(".css") ? "text/css" : "text/javascript";
        response.writeHead(200, { "content-type": type }).end(content);
      } catch {
        response.writeHead(404).end();
      }
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const { port } = server.address() as AddressInfo;
    const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>", {
      url: `http://127.0.0.1:${port}/assistant/apps?tab=details#/artifacts`,
      resources: "usable",
    });
    const globals = globalThis as unknown as Record<string, unknown>;
    const savedGlobals = {
      window: globals.window,
      document: globals.document,
      HTMLLinkElement: globals.HTMLLinkElement,
      MutationObserver: globals.MutationObserver,
      MonacoEnvironment: globals.MonacoEnvironment,
    };

    try {
      globals.window = dom.window;
      globals.document = dom.window.document;
      globals.HTMLLinkElement = dom.window.HTMLLinkElement;
      globals.MutationObserver = dom.window.MutationObserver;

      await expect(prepareTrustedWebRuntime()).rejects.toThrow("compiled Gideon console stylesheet failed to load");
      expect(stylesheetRequests).toBe(1);
      expect(dom.window.document.getElementById("gideon-trusted-console-stylesheet")).toBeNull();

      stylesheetAvailable = true;
      await prepareTrustedWebRuntime();
      const stylesheet = dom.window.document.querySelector<HTMLLinkElement>("#gideon-trusted-console-stylesheet");
      expect(stylesheet?.href).toBe(`http://127.0.0.1:${port}/assistant/assets/gideon-console.css`);
      expect(stylesheetRequests).toBe(2);

      const portalRoot = dom.window.document.createElement("div");
      portalRoot.setAttribute("data-gideon-dialog-portal", "");
      portalRoot.innerHTML = '<div role="alertdialog"></div>';
      dom.window.document.body.append(portalRoot);
      await new Promise((resolve) => dom.window.setTimeout(resolve, 0));
      expect(portalRoot.classList.contains("gideon-trusted-module-dialog-root")).toBe(false);

      const trustedModule = dom.window.document.createElement("main");
      trustedModule.className = "gideon-trusted-module";
      dom.window.document.body.append(trustedModule);
      await new Promise((resolve) => dom.window.setTimeout(resolve, 0));
      expect(portalRoot.classList.contains("gideon-trusted-module-dialog-root")).toBe(true);

      portalRoot.remove();
      await new Promise((resolve) => dom.window.setTimeout(resolve, 0));
      expect(portalRoot.classList.contains("gideon-trusted-module-dialog-root")).toBe(false);
    } finally {
      for (const [key, value] of Object.entries(savedGlobals)) {
        if (value === undefined) delete globals[key];
        else globals[key] = value;
      }
      dom.window.close();
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    }
  });

  it("fails before producing output when a required console stylesheet is absent", async () => {
    const outputDirectory = await mkdtemp(path.join(os.tmpdir(), "gideon-trusted-assets-missing-"));
    outputs.push(outputDirectory);
    await expect(assetBuilder.buildTrustedWebAssets({ outputDirectory, assistantDirectory: assistantRoot, consoleDirectory: path.join(outputDirectory, "missing-console") }))
      .rejects.toThrow("required console theme stylesheet is missing");
    expect((await stat(outputDirectory)).isDirectory()).toBe(true);
    await expect(stat(path.join(outputDirectory, "gideon-console.css"))).rejects.toMatchObject({ code: "ENOENT" });
  });
});
