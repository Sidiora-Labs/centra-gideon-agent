import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

type NoticeGraph = {
  bundles: { output: string; packages: string[] }[];
  packages: { key: string; name: string; version: string }[];
};

const projectRoot = resolve(import.meta.dirname, "../../../../..");
const exportedRoot = resolve(projectRoot, "apps/assistant/dist/web");
const noticeFile = resolve(projectRoot, "apps/console/public/assistant-source-notices.txt");
const packagedNoticeFile = resolve(exportedRoot, "assistant-source-notices.txt");
const graphFile = process.env.ASSISTANT_NOTICE_GRAPH_FILE;

describe("assistant third-party notices", () => {
  it("matches the Expo package graph and shipped notice artifact", () => {
    expect(graphFile).toBeTruthy();
    const graph: NoticeGraph = JSON.parse(readFileSync(resolve(graphFile!), "utf8"));
    const notice = readFileSync(noticeFile);
    const packagedNotice = readFileSync(packagedNoticeFile);

    expect(graph.packages.length).toBeGreaterThan(0);
    expect(graph.bundles.length).toBeGreaterThan(0);
    expect(graph.bundles.every((bundle) => bundle.output.endsWith(".js"))).toBe(true);
    expect(graph.bundles.flatMap((bundle) => bundle.packages).every((key) => graph.packages.some((item) => item.key === key))).toBe(true);
    expect(packagedNotice.equals(notice)).toBe(true);
    for (const item of graph.packages) expect(notice.toString("utf8")).toContain(`${item.name}@${item.version}`);

    const khromaLicense = readFileSync(resolve(projectRoot, "apps/assistant/node_modules/khroma/license"), "utf8")
      .replace(/^The MIT License \(MIT\)\s*/m, "")
      .replace(/\r\n?/g, "\n")
      .trim();
    expect(notice.toString("utf8")).toContain("khroma@2.1.0");
    expect(notice.toString("utf8")).toContain("License: MIT");
    expect(notice.toString("utf8")).toContain(khromaLicense);

    const reactNativeLicense = readFileSync(resolve(projectRoot, "apps/assistant/node_modules/react-native/LICENSE"), "utf8")
      .replace(/\r\n?/g, "\n")
      .trim();
    expect(notice.toString("utf8")).toContain("@react-native/js-polyfills@0.81.5");
    expect(notice.toString("utf8")).toContain("LICENSE (shared repository package react-native)");
    expect(notice.toString("utf8")).toContain(reactNativeLicense);
  });
});
