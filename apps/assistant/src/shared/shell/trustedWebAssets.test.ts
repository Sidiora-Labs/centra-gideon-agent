import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";

const assistantRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const consoleRoot = path.resolve(assistantRoot, "../console");
const assetBuilder = await import(new URL("../../../tooling/buildTrustedWebAssets.mjs", import.meta.url).href);
const execFileAsync = promisify(execFile);
type BuiltAsset = { path: string; bytes: number; sha256: string };
const outputs: string[] = [];

async function chromiumGridLayout(pagePath: string, width: number) {
  const profile = path.join(path.dirname(pagePath), `chromium-profile-${width}`);
  const command = process.env.GIDEON_TEST_CHROMIUM || process.env.CHROMIUM_BIN || "chromium";
  const { stdout } = await execFileAsync(command, [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-background-networking",
    "--no-first-run",
    "--allow-file-access-from-files",
    `--window-size=${width},900`,
    `--user-data-dir=${profile}`,
    "--dump-dom",
    pathToFileURL(pagePath).href,
  ], {
    encoding: "utf8",
    timeout: 30000,
    maxBuffer: 4 * 1024 * 1024,
    env: Object.fromEntries(Object.entries(process.env).filter(([key]) => key !== "OPENAI_API_KEY")),
  });
  const result = stdout.match(/<title>GRID:([^<]+)<\/title>/)?.[1];
  expect(result, `Chromium did not report computed layout at ${width}px`).toBeDefined();
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  return JSON.parse(Buffer.from(result!, "base64").toString("utf8")) as {
    width: number;
    display: string;
    columns: string;
    children: Array<{ x: number; y: number }>;
    outsideDisplay: string;
  };
}

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
    const manifest: { assets: BuiltAsset[] } = JSON.parse(await readFile(path.join(outputDirectory, "manifest.json"), "utf8"));
    expect(manifest.assets).toEqual(assets);
    const monacoSource = await readFile(path.join(assistantRoot, "node_modules/monaco-editor/esm/vs/editor/editor.main.js"), "utf8");
    expect(monacoSource).toContain("languages/definitions/markdown/register.js");
    expect(monacoSource).toContain("languages/features/json/register.js");
    expect(monacoSource).toContain("languages/features/typescript/register.js");

    const monacoBundleFiles = (await readdir(path.join(outputDirectory, "monaco"), { recursive: true }))
      .filter((file) => file.endsWith(".js"));
    expect(monacoBundleFiles.length).toBeGreaterThan(1);
    expect(monacoBundleFiles).toContain("gideon-monaco-editor.api.js");
    for (const asset of assets) {
      const bytes = await readFile(path.join(outputDirectory, asset.path));
      expect(bytes.byteLength).toBe(asset.bytes);
      expect(createHash("sha256").update(bytes).digest("hex")).toBe(asset.sha256);
    }
    expect(assets.length).toBe(manifest.assets.length);
  }, 180000);

  it("discovers a real assistant responsive utility while keeping it scoped to trusted modules", async () => {
    const outputDirectory = await mkdtemp(path.join(os.tmpdir(), "gideon-trusted-assets-responsive-"));
    outputs.push(outputDirectory);
    const slideSource = await readFile(path.join(assistantRoot, "src/features/studio/SlidesWorkspace.web.tsx"), "utf8");
    const responsiveClass = "lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]";
    expect(slideSource).toContain(responsiveClass);

    await assetBuilder.buildTrustedWebAssets({ outputDirectory, assistantDirectory: assistantRoot, consoleDirectory: consoleRoot });
    const css = await readFile(path.join(outputDirectory, "gideon-console.css"), "utf8");
    expect(css).toContain(".gideon-trusted-module .grid");
    expect(css).toContain("lg\\:grid-cols-");
    expect(css).not.toMatch(/(?:^|})\.grid\s*\{/);

    const pagePath = path.join(outputDirectory, "responsive-check.html");
    await writeFile(pagePath, `<!doctype html><html><head><link rel="stylesheet" href="./gideon-console.css"></head><body>
      <div class="gideon-trusted-module" data-gideon-module="studio/slides">
        <div id="layout" class="grid gap-4 ${responsiveClass}"><div style="height:20px">one</div><div style="height:20px">two</div></div>
      </div>
      <div id="outside" class="grid gap-4 ${responsiveClass}"><div>outside</div></div>
      <script>const grid=document.querySelector('#layout');const rects=[...grid.children].map(item=>{const r=item.getBoundingClientRect();return{x:r.x,y:r.y}});const layout={width:innerWidth,display:getComputedStyle(grid).display,columns:getComputedStyle(grid).gridTemplateColumns,children:rects,outsideDisplay:getComputedStyle(document.querySelector('#outside')).display};document.title='GRID:'+btoa(JSON.stringify(layout));</script>
    </body></html>`, "utf8");

    const desktop = await chromiumGridLayout(pagePath, 1280);
    expect(desktop.width).toBeGreaterThanOrEqual(1024);
    expect(desktop.display).toBe("grid");
    expect(desktop.columns.trim().split(/\s+/)).toHaveLength(2);
    expect(desktop.children[1].x).toBeGreaterThan(desktop.children[0].x);
    expect(desktop.children[1].y).toBe(desktop.children[0].y);
    expect(desktop.outsideDisplay).toBe("block");

    const phone = await chromiumGridLayout(pagePath, 390);
    expect(phone.width).toBeLessThan(1024);
    expect(phone.display).toBe("grid");
    expect(phone.children[1].x).toBe(phone.children[0].x);
    expect(phone.children[1].y).toBeGreaterThan(phone.children[0].y);
    expect(phone.outsideDisplay).toBe("block");
  }, 180000);

  it("fails before producing output when a required console stylesheet is absent", async () => {
    const outputDirectory = await mkdtemp(path.join(os.tmpdir(), "gideon-trusted-assets-missing-"));
    outputs.push(outputDirectory);
    await expect(assetBuilder.buildTrustedWebAssets({ outputDirectory, assistantDirectory: assistantRoot, consoleDirectory: path.join(outputDirectory, "missing-console") }))
      .rejects.toThrow("required console theme stylesheet is missing");
    expect((await stat(outputDirectory)).isDirectory()).toBe(true);
    await expect(stat(path.join(outputDirectory, "gideon-console.css"))).rejects.toMatchObject({ code: "ENOENT" });
  });
});
