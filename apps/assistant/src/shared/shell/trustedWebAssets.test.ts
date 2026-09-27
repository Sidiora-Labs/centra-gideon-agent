import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
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
