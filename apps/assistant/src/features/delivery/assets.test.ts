import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const projectRoot = resolve(import.meta.dirname, "../../..");
const exportedRoot = join(projectRoot, "dist/web");
const exportedIndex = join(exportedRoot, "index.html");
const basePath = "/assistant";

function exportedFiles(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    return entry.isDirectory() ? exportedFiles(path) : [path];
  });
}

describe("packaged assistant web assets", () => {
  it("exports the SPA entry, bundled resources, and required source notice", () => {
    const html = readFileSync(exportedIndex, "utf8");
    const urls = [...html.matchAll(/(?:src|href)="([^"#]+)"/g)]
      .map((match) => match[1])
      .filter((url) => url.startsWith("/"));

    expect(urls.length).toBeGreaterThan(0);
    expect(urls.every((url) => url.startsWith(`${basePath}/`))).toBe(true);
    for (const url of urls) {
      const pathname = url.split(/[?#]/, 1)[0].slice(basePath.length + 1);
      expect(readFileSync(join(exportedRoot, pathname))).toBeTruthy();
    }

    const files = exportedFiles(exportedRoot);
    expect(files.some((path) => path.endsWith(".js"))).toBe(true);
    const notice = readFileSync(join(exportedRoot, "assistant-source-notices.txt"));
    const sourceNotice = readFileSync(join(projectRoot, "../console/public/assistant-source-notices.txt"));
    expect(notice.equals(sourceNotice)).toBe(true);
  });

  it("keeps all emitted files inside the nested artifact directory", () => {
    for (const file of exportedFiles(exportedRoot)) {
      expect(relative(exportedRoot, file).startsWith("..")).toBe(false);
      expect(statSync(file).isFile()).toBe(true);
    }
  });
});
