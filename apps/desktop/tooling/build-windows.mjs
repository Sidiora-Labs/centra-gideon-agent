import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const DEFAULT_HOSTED_ORIGIN = "https://gideon.centra.ag";

export function hostedOrigin(value) {
  if (typeof value !== "string" || value !== value.trim() || !/^https:\/\/[^/?#\s]+\/?$/.test(value)) {
    throw new Error("GIDEON_CLOUD_URL must be an explicit HTTPS origin without a path, query, or fragment");
  }
  let url;
  try { url = new URL(value); }
  catch { throw new Error("GIDEON_CLOUD_URL must be a valid HTTPS origin"); }
  if (url.protocol !== "https:" || !url.hostname || url.username || url.password) {
    throw new Error("GIDEON_CLOUD_URL must be an HTTPS origin without credentials");
  }
  return url.origin;
}

export function configuredOrigin(override = DEFAULT_HOSTED_ORIGIN) {
  return hostedOrigin(override);
}

export function windowsConfig(base, configPath) {
  assert.equal(base.appId, "io.gideon.app");
  assert.equal(base.productName, "Gideon");
  return {
    ...base,
    extraResources: [{ from: configPath, to: "hosted-config.json" }],
    win: { ...base.win, icon: "assets/gideon.ico", target: [{ target: "nsis", arch: ["x64"] }] },
    nsis: { ...base.nsis, artifactName: "Gideon-Setup-${version}-${arch}.${ext}" },
    directories: { ...base.directories, output: "dist" },
    publish: null,
  };
}

export async function checksumManifest(artifacts, outputDir) {
  const installers = artifacts.filter((artifact) => artifact.endsWith(".exe"));
  if (installers.length !== 1) throw new Error(`Expected one Windows NSIS installer, received ${installers.length}`);
  const installer = installers[0];
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(installer)) hash.update(chunk);
  const manifest = path.join(outputDir, "SHA256SUMS.txt");
  await writeFile(manifest, `${hash.digest("hex")}  ${path.basename(installer)}\n`);
  return { installer, manifest };
}

export async function buildWindows(url) {
  const hostedUrl = configuredOrigin(url);
  const pkg = JSON.parse(await readFile(path.join(projectDir, "package.json"), "utf8"));
  const configDir = await mkdtemp(path.join(os.tmpdir(), "gideon-win-"));
  const configPath = path.join(configDir, "hosted-config.json");
  const outputDir = path.join(projectDir, "dist");
  try {
    await writeFile(configPath, `${JSON.stringify({ schemaVersion: 1, mode: "hosted", hostedUrl })}\n`);
    await mkdir(outputDir, { recursive: true });
    const { build, Platform, Arch } = await import("electron-builder");
    const electronVersion = JSON.parse(await readFile(path.join(projectDir, "node_modules/electron/package.json"), "utf8")).version;
    const artifacts = await build({
      projectDir,
      config: { ...windowsConfig(pkg.build, configPath), electronVersion },
      targets: Platform.WINDOWS.createTarget("nsis", Arch.x64),
      publish: "never",
    });
    return await checksumManifest(artifacts, outputDir);
  } finally {
    await rm(configDir, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let completed = false;
  process.on("exit", () => {
    if (!completed) {
      process.stderr.write("Windows installer build stopped before checksum generation.\n");
      process.exitCode = 1;
    }
  });
  buildWindows(process.env.GIDEON_CLOUD_URL).then(({ installer, manifest }) => {
    completed = true;
    process.stdout.write(`Windows installer: ${installer}\nSHA-256 manifest: ${manifest}\n`);
    if (!process.env.CSC_LINK && !process.env.WIN_CSC_LINK && !process.env.CSC_NAME) {
      process.stdout.write("Signing credentials absent; installer is unsigned.\n");
    }
  }).catch((error) => { completed = true; process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
}
