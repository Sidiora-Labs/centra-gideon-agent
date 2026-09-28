import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const assistantRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(assistantRoot, "../..");
const sectionMarker = "\nGideon assistant web notices\n==============================\n";
const pinnedSourceMarker = "\nPinned npm license source terms\n-------------------------------\n";
const licenseName = /^(?:licen[cs]e|copying|unlicense|notice|third[-_ ]?party[-_ ]?(?:licen[cs]es?|notices?))(?:[-_.][\w.-]*)?$/i;
const nonText = /\.(?:[cm]?[jt]sx?|json|map)$/i;
const normalize = (value) => value.replace(/^\ufeff/, "").replace(/\r\n?/g, "\n").replace(/[ \t]+$/gm, "").replace(/\n+$/, "");

function cliArguments(args) {
  const values = new Map();
  for (let index = 0; index < args.length; index += 2) {
    if (!args[index]?.startsWith("--") || !args[index + 1]) throw new Error("expected named option and value pairs");
    values.set(args[index].slice(2), path.resolve(process.cwd(), args[index + 1]));
  }
  for (const name of ["bundle-dir", "asset-manifest", "source-manifest", "font-notices", "notice-file"]) {
    if (!values.has(name)) throw new Error(`usage: node tooling/thirdPartyNotices.mjs --bundle-dir <directory> --asset-manifest <file> --source-manifest <file> --font-notices <file> --notice-file <file> [--graph-file <file>]`);
  }
  return Object.fromEntries(values);
}

function filesUnder(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? filesUnder(file) : entry.isFile() ? [file] : [];
  });
}

function packageKeysForSources(sources, lock) {
  const modules = sources.map((source) => {
    const absolute = source.startsWith("file:") ? fileURLToPath(source) : path.resolve(source);
    try { return realpathSync(absolute); }
    catch { return absolute; }
  }).filter((source) => source.includes(`${path.sep}node_modules${path.sep}`));
  const candidates = Object.keys(lock.packages ?? {})
    .filter((key) => key.startsWith("node_modules/"))
    .map((key) => ({ key, root: path.join(assistantRoot, key) }))
    .filter(({ root }) => existsSync(root))
    .map(({ key, root }) => ({ key, root: realpathSync(root) }))
    .sort((a, b) => b.root.length - a.root.length || a.key.localeCompare(b.key));
  const bundled = new Set();
  for (const modulePath of modules) {
    const match = candidates.find(({ root }) => modulePath === root || modulePath.startsWith(`${root}${path.sep}`));
    if (!match) throw new Error(`Expo bundled a node_modules module missing from the assistant lockfile: ${modulePath}`);
    bundled.add(match.key);
  }
  return [...bundled].sort();
}

function mapGraphs(bundleDirectory, lock) {
  const mapFiles = filesUnder(bundleDirectory).filter((file) => file.endsWith(".map"));
  if (!mapFiles.length) throw new Error(`Expo web export has no source maps: ${bundleDirectory}`);
  const graphs = new Map();
  const packageKeys = new Set();
  for (const mapFile of mapFiles) {
    const map = JSON.parse(readFileSync(mapFile, "utf8"));
    if (!Array.isArray(map.sources)) throw new Error(`invalid Expo source map: ${mapFile}`);
    const base = map.sourceRoot ? path.resolve(path.dirname(mapFile), map.sourceRoot) : path.dirname(mapFile);
    const sources = map.sources.map((source) => {
      if (typeof source !== "string") throw new Error(`invalid source entry in Expo source map: ${mapFile}`);
      return source.startsWith("file:") ? fileURLToPath(source) : path.resolve(base, source);
    });
    const keys = packageKeysForSources(sources, lock);
    for (const key of keys) packageKeys.add(key);
    graphs.set(path.relative(bundleDirectory, mapFile.slice(0, -4)).split(path.sep).join("/"), keys);
  }
  if (!packageKeys.size) throw new Error("Expo web source maps contain no npm package modules");
  return { graphs, packageKeys };
}

function pinnedLicenseSources(notices) {
  const start = notices.indexOf(pinnedSourceMarker);
  if (start < 0) return new Map();
  const end = notices.indexOf(sectionMarker, start);
  const records = notices.slice(start + pinnedSourceMarker.length, end < 0 ? undefined : end);
  return new Map(records.split(/\n@@ /).filter(Boolean).map((block) => {
    const [identity, ...body] = block.split("\n");
    const match = /^(.*)@(\d+\.\d+\.\d+)$/.exec(identity);
    const source = body.join("\n").match(/^Source: (https:\/\/\S+)$/m)?.[1];
    const license = body.join("\n").match(/^License: (.+)$/m)?.[1];
    const terms = normalize(body.join("\n").replace(/^Source: https:\/\/\S+\nLicense: [^\n]+\n\n/, ""));
    if (!match || !source || !license || !terms) throw new Error(`invalid pinned npm license source record: ${identity}`);
    return [`${match[1]}@${match[2]}`, { source, license, terms }];
  }));
}

function licenseForPackage(lockKey, lock, pinnedSources) {
  const root = path.join(assistantRoot, lockKey);
  const metadata = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
  const locked = lock.packages?.[lockKey];
  const declaredLicense = typeof locked?.license === "string" ? locked.license.trim()
    : typeof metadata.license === "string" ? metadata.license.trim()
    : typeof metadata.license?.type === "string" ? metadata.license.type.trim()
      : Array.isArray(metadata.licenses) ? metadata.licenses.map((item) => typeof item === "string" ? item : item?.type).filter(Boolean).join(" OR ")
        : "";
  if (!locked?.version) throw new Error(`bundled npm package has no lock version: ${lockKey}`);
  const files = readdirSync(root)
    .filter((name) => licenseName.test(name) && !nonText.test(name))
    .sort((a, b) => (/^(?:licen[cs]e|copying|unlicense)/i.test(a) ? 0 : 1) - (/^(?:licen[cs]e|copying|unlicense)/i.test(b) ? 0 : 1) || a.localeCompare(b));
  let texts = files.map((file) => ({ file, text: normalize(readFileSync(path.join(root, file), "utf8")) }));
  if (!texts.length) {
    const repository = typeof metadata.repository === "string" ? metadata.repository : metadata.repository?.url;
    const normalizeRepository = (value) => typeof value === "string" ? value.trim().replace(/^git\+/, "").replace(/\.git$/, "") : "";
    const repositoryId = normalizeRepository(repository);
    const siblingLicense = Object.keys(lock.packages ?? {}).filter((key) => key.startsWith("node_modules/") && key !== lockKey)
      .map((key) => ({ key, metadata: path.join(assistantRoot, key, "package.json"), root: path.join(assistantRoot, key) }))
      .filter(({ metadata: file }) => existsSync(file))
      .map((candidate) => ({ ...candidate, package: JSON.parse(readFileSync(candidate.metadata, "utf8")) }))
      .find((candidate) => {
        const siblingRepository = typeof candidate.package.repository === "string" ? candidate.package.repository : candidate.package.repository?.url;
        const siblingLicenseId = typeof candidate.package.license === "string" ? candidate.package.license.trim() : candidate.package.license?.type?.trim();
        return repositoryId && normalizeRepository(siblingRepository) === repositoryId
          && candidate.package.version === locked.version && siblingLicenseId === declaredLicense
          && existsSync(path.join(candidate.root, "LICENSE"));
      });
    if (siblingLicense) {
      texts = [{ file: `LICENSE (shared repository package ${siblingLicense.package.name})`, text: normalize(readFileSync(path.join(siblingLicense.root, "LICENSE"), "utf8")) }];
    }
  }
  const pinned = pinnedSources.get(`${metadata.name}@${locked.version}`);
  if (!texts.length && pinned && pinned.license === declaredLicense) {
    texts = [{ file: "LICENSE (pinned upstream source)", text: pinned.terms }];
  }
  const textLicense = texts.map(({ text }) => text.match(/^SPDX-License-Identifier:\s*([^\r\n]+)$/m)?.[1]?.trim()
    ?? text.match(/^(?:The )?([A-Za-z0-9.+-]+) License \(\1\)$/m)?.[1])
    .find(Boolean);
  const license = declaredLicense || textLicense;
  if (!license) throw new Error(`bundled npm package has no declared license or explicit license identifier in its notice text: ${lockKey}`);
  if (!texts.length) throw new Error(`bundled npm package has no license text in its archive or a verified same-version repository package: ${lockKey}`);
  const rawRepository = typeof metadata.repository === "string" ? metadata.repository : metadata.repository?.url;
  let source = typeof rawRepository === "string" ? rawRepository.trim() : "";
  const short = /^(?:(github|gitlab|bitbucket):)?([\w.-]+\/[\w.-]+)$/.exec(source);
  if (short) source = `https://${{ github: "github.com", gitlab: "gitlab.com", bitbucket: "bitbucket.org" }[short[1] ?? "github"]}/${short[2]}`;
  else source = source.replace(/^git\+/, "").replace(/\.git$/, "").replace(/^http:\/\//, "https://");
  if (!/^https:\/\//.test(source) && /^https:\/\//.test(metadata.homepage ?? "")) source = metadata.homepage;
  if (!/^https:\/\//.test(source)) source = `https://www.npmjs.com/package/${encodeURIComponent(metadata.name)}/v/${locked.version}`;
  if (pinned && texts.some(({ file }) => file === "LICENSE (pinned upstream source)")) source = pinned.source;
  const terms = texts.map(({ file, text }) => `${file}\n${"-".repeat(file.length)}\n${text}`).join("\n\n");
  return { name: metadata.name, version: locked.version, license, source, terms };
}

function addPackageKey(packageKeys, lock, name) {
  const key = Object.keys(lock.packages ?? {}).find((candidate) => candidate === `node_modules/${name}`);
  if (!key || !lock.packages[key].version) throw new Error(`trusted assistant asset package is missing from the assistant lockfile: ${name}`);
  packageKeys.add(key);
}

function fontLicenseText(notices) {
  const start = notices.indexOf("SIL OPEN FONT LICENSE Version 1.1");
  if (start < 0) throw new Error("font notice source does not contain the SIL Open Font License");
  const endText = "OTHER DEALINGS IN THE FONT SOFTWARE.";
  const end = notices.indexOf(endText, start);
  if (end < 0) throw new Error("font notice source has an incomplete SIL Open Font License");
  return normalize(notices.slice(start, end + endText.length));
}

function fontAttribution(notices, sourcePath) {
  const block = notices.split(/\n\s*\n/).find((part) => part.includes(`Path: ${sourcePath}`));
  if (!block) throw new Error(`font notice source has no attribution for ${sourcePath}`);
  const field = (name) => block.match(new RegExp(`^${name}: (.+)$`, "m"))?.[1];
  const source = field("Source");
  const version = field("Version");
  const license = field("License");
  const copyright = block.match(/^Copyright\s+(.+)$/m)?.[1];
  if (!source || !version || !license || !copyright) throw new Error(`font notice source has incomplete attribution for ${sourcePath}`);
  return { source, version, license, copyright };
}

function renderPackageNotices(packageKeys, lock, pinnedSources) {
  const packages = [...packageKeys].map((key) => licenseForPackage(key, lock, pinnedSources))
    .sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));
  return packages.map((pkg) => [
    `${pkg.name}@${pkg.version}`,
    `Source: ${pkg.source}`,
    `License: ${pkg.license}`,
    "",
    pkg.terms,
  ].join("\n")).join("\n\n");
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function inventoryAssets({ bundleDirectory, graphs, lock, sourceManifest, trustedManifest, fontNotices }) {
  const sourceRecords = JSON.parse(readFileSync(sourceManifest, "utf8")).assets ?? [];
  const sourceByPath = new Map(sourceRecords.map((asset) => [asset.path, asset]));
  const trusted = JSON.parse(readFileSync(trustedManifest, "utf8")).assets ?? [];
  const trustedByPath = new Map(trusted.map((asset) => [`assets/${asset.path}`, asset]));
  const fontNoticesText = readFileSync(fontNotices, "utf8");
  const assetRows = [];
  const mapFiles = filesUnder(bundleDirectory).filter((file) => file.endsWith(".map"));
  for (const mapFile of mapFiles) {
    const output = mapFile.slice(0, -4);
    if (!existsSync(output) || !output.endsWith(".js")) continue;
    const source = readFileSync(output, "utf8").replace(/(?:\r?\n)?\/\/[#@]\s*sourceMappingURL=[^\r\n]*\s*$/, "");
    writeFileSync(output, source, "utf8");
  }
  for (const mapFile of mapFiles) rmSync(mapFile, { force: true });

  for (const file of filesUnder(bundleDirectory).sort()) {
    const relativePath = path.relative(bundleDirectory, file).split(path.sep).join("/");
    const bytes = readFileSync(file);
    const trustedAsset = trustedByPath.get(relativePath);
    let source = "Gideon assistant web source";
    let version = "generated web asset";
    let notice = "Gideon source is available with this distribution";
    let copyright;
    if (trustedAsset) {
      source = "Gideon trusted web asset build";
      version = "assistant web build";
      notice = "See the package and font notices in this file";
    }
    if (relativePath.startsWith("assets/fonts/")) {
      const sourcePath = `apps/console/public/fonts/${path.basename(relativePath)}`;
      const record = sourceByPath.get(sourcePath);
      if (!record?.source || !record?.version || !record?.sha256 || !record?.notice) throw new Error(`assistant font has no source record: ${sourcePath}`);
      if (sha256(bytes) !== record.sha256) throw new Error(`assistant font bytes differ from the recorded source: ${relativePath}`);
      const attribution = fontAttribution(fontNoticesText, sourcePath);
      if (attribution.version.split(/\s+/).at(-1) !== record.version) throw new Error(`font source version differs from its asset record: ${sourcePath}`);
      source = attribution.source;
      version = attribution.version;
      copyright = attribution.copyright;
      notice = `${attribution.license}; see the font license text below`;
    } else if (relativePath.endsWith(".js.map")) {
      throw new Error(`source maps must not be included in the shipped assistant web output: ${relativePath}`);
    } else if (relativePath.startsWith("assets/workers/") || relativePath.startsWith("assets/monaco/")) {
      const key = "node_modules/monaco-editor";
      if (!lock.packages[key]) throw new Error("Monaco assets are missing from the assistant lockfile");
      source = `monaco-editor@${lock.packages[key].version}`;
      version = lock.packages[key].version;
      notice = "See the Monaco Editor npm notice in this file";
    } else if (relativePath.endsWith(".js")) {
      const packageKeys = graphs.get(relativePath) ?? [];
      const packageLabels = packageKeys.map((key) => `${lock.packages[key].name ?? key.slice("node_modules/".length)}@${lock.packages[key].version}`);
      source = packageLabels.length ? `Expo/Metro app bundle containing ${packageLabels.join(", ")}` : "Expo/Metro generated app bundle";
      version = `Expo ${lock.packages["node_modules/expo"].version}`;
      notice = packageLabels.length ? "See the listed npm package notices in this file" : "Generated from Gideon assistant source";
    } else if (relativePath.endsWith(".css") && /katex/i.test(relativePath)) {
      const key = "node_modules/katex";
      if (!lock.packages[key]) throw new Error("KaTeX stylesheet is missing from the assistant lockfile");
      source = `katex@${lock.packages[key].version}`;
      version = lock.packages[key].version;
      notice = "See the KaTeX npm notice in this file";
    } else if (relativePath.endsWith(".css")) {
      source = "Gideon assistant and console theme source";
      version = "assistant web build";
    } else if (relativePath === "index.html" || relativePath === "metadata.json") {
      source = "Expo/Metro generated assistant web entry";
      version = `Expo ${lock.packages["node_modules/expo"].version}`;
    }
    assetRows.push({ path: relativePath, source, version, sha256: sha256(bytes), notice, ...(copyright ? { copyright } : {}) });
  }
  return assetRows;
}

export function buildNoticeArtifacts({ bundleDirectory, assetManifest, sourceManifest, fontNotices, noticeFile, graphFile }) {
  const lock = JSON.parse(readFileSync(path.join(assistantRoot, "package-lock.json"), "utf8"));
  const { graphs, packageKeys } = mapGraphs(bundleDirectory, lock);
  addPackageKey(packageKeys, lock, "monaco-editor");
  if (filesUnder(bundleDirectory).some((file) => /katex[^/]*\.css$/i.test(file))) addPackageKey(packageKeys, lock, "katex");
  if (graphFile) {
    const packages = [...packageKeys].map((key) => {
      const metadata = JSON.parse(readFileSync(path.join(assistantRoot, key, "package.json"), "utf8"));
      return { key, name: metadata.name, version: lock.packages[key].version };
    }).sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));
    const bundles = [...graphs].map(([output, keys]) => ({ output, packages: keys }))
      .sort((a, b) => a.output.localeCompare(b.output));
    mkdirSync(path.dirname(graphFile), { recursive: true });
    writeFileSync(graphFile, `${JSON.stringify({ version: 1, bundles, packages }, null, 2)}\n`, "utf8");
  }
  const noticeText = readFileSync(noticeFile, "utf8");
  const pinnedSources = pinnedLicenseSources(noticeText);
  const assets = inventoryAssets({ bundleDirectory, graphs, lock, sourceManifest, trustedManifest: assetManifest, fontNotices });
  const packages = [...packageKeys].map((key) => {
    const metadata = JSON.parse(readFileSync(path.join(assistantRoot, key, "package.json"), "utf8"));
    return { name: metadata.name, version: lock.packages[key].version };
  }).sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));
  const fontAssets = assets.filter((asset) => asset.path.startsWith("assets/fonts/"));
  const fontNotice = fontAssets.length ? `\n\nFont license\n------------\n\n${fontLicenseText(readFileSync(fontNotices, "utf8"))}\n` : "";
  const base = noticeText.includes(sectionMarker) ? noticeText.slice(0, noticeText.indexOf(sectionMarker)) : noticeText;
  if (!base.endsWith("\n")) throw new Error("assistant source notice must end with a newline");
  const assetText = assets.map((asset) => `${asset.path}\n  Source: ${asset.source}\n  Version: ${asset.version}\n  SHA-256: ${asset.sha256}\n${asset.copyright ? `  ${asset.copyright}\n` : ""}  Notice: ${asset.notice}`).join("\n\n");
  const generated = `Assistant web asset inventory\n------------------------------\n\n${assetText}\n\nThis inventory omits the notice file itself because a document cannot contain its own stable digest.\n\nAssistant npm package notices\n------------------------------\n\n${renderPackageNotices(packageKeys, lock, pinnedSources)}${fontNotice}`;
  const finalNotice = `${base}${sectionMarker}${generated}`;
  writeFileSync(noticeFile, finalNotice, "utf8");
  return { packages, assets };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const options = cliArguments(process.argv.slice(2));
    const result = buildNoticeArtifacts({
      bundleDirectory: options["bundle-dir"],
      assetManifest: options["asset-manifest"],
      sourceManifest: options["source-manifest"],
      fontNotices: options["font-notices"],
      noticeFile: options["notice-file"],
      graphFile: options["graph-file"] ?? process.env.ASSISTANT_NOTICE_GRAPH_FILE,
    });
    console.log(`Recorded ${result.packages.length} npm packages and ${result.assets.length} shipped assistant web assets in ${options["notice-file"]}`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
