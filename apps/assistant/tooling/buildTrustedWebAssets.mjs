import { createHash } from "node:crypto";
import { cp, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build as bundle } from "esbuild";
import tailwindcss from "@tailwindcss/vite";
import postcss from "postcss";
import { build as viteBuild } from "vite";

const assistantRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const consoleRoot = path.resolve(assistantRoot, "../console");
const fontFiles = [
  "dm-sans.woff2",
  "inter.woff2",
  "jetbrains-mono.woff2",
  "google-sans-flex.woff2",
  "google-sans-code.woff2",
];
const workerEntries = {
  editor: "editor/editor.worker.js",
  json: "language/json/json.worker.js",
  css: "language/css/css.worker.js",
  html: "language/html/html.worker.js",
  typescript: "language/typescript/ts.worker.js",
};
const monacoEntry = "editor/editor.api.js";

function parseArguments(args) {
  const index = args.indexOf("--out-dir");
  if (index < 0 || !args[index + 1]) throw new Error("usage: node tooling/buildTrustedWebAssets.mjs --out-dir <directory>");
  return path.resolve(process.cwd(), args[index + 1]);
}

async function requireFile(filePath, label) {
  let info;
  try { info = await stat(filePath); }
  catch { throw new Error(`required ${label} is missing: ${filePath}`); }
  if (!info.isFile() || info.size === 0) throw new Error(`required ${label} is empty: ${filePath}`);
  return filePath;
}

function outputEntries(output) {
  return (Array.isArray(output) ? output : [output]).flatMap((item) => item.output ?? []);
}

function splitSelectorList(selectorList) {
  const selectors = [];
  let start = 0;
  let depth = 0;
  let quote;
  let escaped = false;
  for (let index = 0; index < selectorList.length; index += 1) {
    const character = selectorList[index];
    if (escaped) { escaped = false; continue; }
    if (character === "\\") { escaped = true; continue; }
    if (quote) { if (character === quote) quote = undefined; continue; }
    if (character === "'" || character === '"') { quote = character; continue; }
    if (character === "(" || character === "[") depth += 1;
    else if (character === ")" || character === "]") depth -= 1;
    else if (character === "," && depth === 0) {
      selectors.push(selectorList.slice(start, index).trim());
      start = index + 1;
    }
  }
  selectors.push(selectorList.slice(start).trim());
  return selectors.filter(Boolean);
}

function scopeSelector(selector) {
  const scopes = [".gideon-trusted-module", ".gideon-trusted-module-dialog-root"];
  const rootToken = /^(?:html|body|:root|:host)(?=$|[\s.#:[>+~])/;
  const leading = selector.match(rootToken)?.[0];
  if (leading) {
    const remainder = selector.slice(leading.length).replace(/(^|[\s>+~])(?:html|body|:root|:host)(?=$|[\s.#:[>+~])/g, "$1");
    return scopes.map((scope) => `${scope}${remainder}`).join(",");
  }
  if (/^\.(?:light|dark)(?=$|[\s.#:[>+~])/.test(selector)) {
    return scopes.flatMap((scope) => [`${scope}${selector}`, `${scope} ${selector}`]).join(",");
  }
  return scopes.map((scope) => `${scope} ${selector}`).join(",");
}

function scopeConsoleCss(css) {
  const root = postcss.parse(css);
  root.walkRules((rule) => {
    let ancestor = rule.parent;
    while (ancestor && ancestor.type !== "root") {
      if (ancestor.type === "atrule" && /(?:^|-)keyframes$/i.test(ancestor.name)) return;
      ancestor = ancestor.parent;
    }
    rule.selector = splitSelectorList(rule.selector).map(scopeSelector).join(",");
  });
  root.append(postcss.parse(
    '.gideon-trusted-module[data-gideon-module="artifacts/editor"]{display:flex;flex:1 1 auto;flex-direction:column;width:100%;height:100%;min-width:0;min-height:0;overflow:hidden}' +
    '.gideon-trusted-module[data-gideon-module="artifacts/editor"]>*{flex:1 1 auto;min-width:0;min-height:0}',
  ).nodes);
  return root.toString();
}

export async function buildTrustedWebAssets({ outputDirectory, assistantDirectory = assistantRoot, consoleDirectory = consoleRoot }) {
  if (!outputDirectory) throw new Error("an output directory is required for trusted web assets");
  const outDir = path.resolve(outputDirectory);
  if (outDir === path.parse(outDir).root || outDir === path.resolve(assistantDirectory) || outDir === path.resolve(consoleDirectory)) {
    throw new Error("trusted web asset output must be a dedicated directory");
  }
  const themePath = path.join(consoleDirectory, "src/shared/theme/tokens.css");
  const fontsPath = path.join(consoleDirectory, "src/shared/theme/fonts.css");
  const shellPath = path.join(consoleDirectory, "src/app/shell/shell.css");
  const fontsDirectory = path.join(consoleDirectory, "public/fonts");
  const monacoDirectory = path.join(assistantDirectory, "node_modules/monaco-editor/esm/vs");
  const sourceFiles = [
    [themePath, "console theme stylesheet"],
    [fontsPath, "console font stylesheet"],
    [shellPath, "console shell stylesheet"],
    ...fontFiles.map((font) => [path.join(fontsDirectory, font), `console font ${font}`]),
    ...Object.entries(workerEntries).map(([family, entry]) => [path.join(monacoDirectory, entry), `Monaco ${family} worker source`]),
    [path.join(monacoDirectory, monacoEntry), "Monaco editor API source"],
  ];
  for (const [filePath, label] of sourceFiles) await requireFile(filePath, label);

  const tokens = await readFile(themePath, "utf8");
  if (!/^@import\s+["']tailwindcss["'];\s*$/m.test(tokens) || !/^@import\s+["']\.\/fonts\.css["'];\s*$/m.test(tokens)) {
    throw new Error("the console theme no longer declares its Tailwind and font stylesheet inputs");
  }
  const themeBody = tokens
    .replace(/^@import\s+["']tailwindcss["'];\s*$/m, "")
    .replace(/^@import\s+["']\.\/fonts\.css["'];\s*$/m, "");
  const virtualCss = path.join(assistantDirectory, "tooling", `.trusted-web-assets-${process.pid}.css`);
  const cssSource = [
    '@import "tailwindcss" source("../../console/src");',
    `@import "${fontsPath.replaceAll("\\", "/")}";`,
    themeBody,
    `@import "${shellPath.replaceAll("\\", "/")}";`,
  ].join("\n");
  await writeFile(virtualCss, cssSource, "utf8");

  try {
    const viteOutput = await viteBuild({
      configFile: false,
      root: assistantDirectory,
      plugins: [tailwindcss()],
      resolve: {
        alias: [{ find: "tailwindcss", replacement: path.join(assistantDirectory, "node_modules/tailwindcss") }],
      },
      build: {
        write: false,
        emptyOutDir: false,
        cssCodeSplit: true,
        cssMinify: true,
        rollupOptions: { input: virtualCss, output: { assetFileNames: "[name][extname]", entryFileNames: "[name].js" } },
      },
    });
    const cssAsset = outputEntries(viteOutput).find((entry) => entry.type === "asset" && String(entry.fileName).endsWith(".css"));
    if (!cssAsset) throw new Error("Tailwind build produced no compiled console stylesheet");
    let css = typeof cssAsset.source === "string" ? cssAsset.source : Buffer.from(cssAsset.source).toString("utf8");
    if (!css.includes("--color-canvas") || !css.includes(".h-full") || !css.includes("gideon-workspace")) {
      throw new Error("compiled console stylesheet is missing required theme, utility, or shell rules");
    }
    css = css.replace(/url\((['"]?)\/fonts\/([^)'"\s]+)\1\)/g, "url($1./fonts/$2$1)");
    css = scopeConsoleCss(css);

    const workerOutDir = path.join(outDir, "workers");
    await mkdir(outDir, { recursive: true });
    await rm(path.join(outDir, "fonts"), { recursive: true, force: true });
    await rm(workerOutDir, { recursive: true, force: true });
    await rm(path.join(outDir, "gideon-console.css"), { force: true });
    await rm(path.join(outDir, "monaco"), { recursive: true, force: true });
    await rm(path.join(outDir, "manifest.json"), { force: true });
    await mkdir(path.join(outDir, "fonts"), { recursive: true });
    await mkdir(workerOutDir, { recursive: true });
    const monacoOutDir = path.join(outDir, "monaco");
    await mkdir(monacoOutDir, { recursive: true });
    await writeFile(path.join(outDir, "gideon-console.css"), css, "utf8");
    for (const font of fontFiles) await cp(path.join(fontsDirectory, font), path.join(outDir, "fonts", font));

    await bundle({
      absWorkingDir: assistantDirectory,
      entryPoints: Object.fromEntries(Object.entries(workerEntries).map(([family, entry]) => [
        family,
        path.join(monacoDirectory, entry),
      ])),
      outdir: workerOutDir,
      entryNames: "gideon-monaco-[name].worker",
      bundle: true,
      format: "esm",
      platform: "browser",
      target: "es2022",
      legalComments: "eof",
      sourcemap: false,
    });

    const monacoOutputDirectory = path.join(assistantDirectory, "tooling", `.trusted-monaco-${process.pid}`);
    const monacoOutput = await bundle({
        absWorkingDir: assistantDirectory,
        entryPoints: [path.join(monacoDirectory, monacoEntry)],
        outdir: monacoOutputDirectory,
        entryNames: "gideon-monaco-editor.api",
        chunkNames: "gideon-monaco-[name]-[hash]",
        assetNames: "gideon-monaco-[name]-[hash]",
        bundle: true,
        splitting: true,
        format: "esm",
        platform: "browser",
        target: "es2022",
        legalComments: "eof",
        sourcemap: false,
        loader: { ".woff": "file", ".woff2": "file", ".ttf": "file", ".svg": "file" },
        write: false,
      });
    for (const file of monacoOutput.outputFiles ?? []) {
      const relativePath = path.relative(monacoOutputDirectory, file.path);
      if (!relativePath || relativePath.startsWith("..") || path.isAbsolute(relativePath)) throw new Error("Monaco editor API emitted an invalid asset path");
      await mkdir(path.dirname(path.join(monacoOutDir, relativePath)), { recursive: true });
      await writeFile(path.join(monacoOutDir, relativePath), file.contents);
    }

    const generatedFiles = await readdir(outDir, { recursive: true });
    const records = [];
    for (const relativePath of ["gideon-console.css", ...fontFiles.map((font) => `fonts/${font}`), ...Object.keys(workerEntries).map((family) => `workers/gideon-monaco-${family}.worker.js`), "monaco/gideon-monaco-editor.api.js", "monaco/gideon-monaco-editor.api.css"]) {
      const filePath = path.join(outDir, relativePath);
      await requireFile(filePath, `built trusted web asset ${relativePath}`);
      const content = await readFile(filePath);
      records.push({ path: relativePath, bytes: content.length, sha256: createHash("sha256").update(content).digest("hex") });
    }
    const monacoFiles = await readdir(monacoOutDir, { recursive: true });
    const monacoJsFiles = monacoFiles.filter((file) => file.endsWith(".js"));
    if (monacoJsFiles.length < 1 || !monacoJsFiles.includes("gideon-monaco-editor.api.js")) throw new Error("Monaco editor API ESM bundle is incomplete");
    for (const relativePath of monacoJsFiles.filter((file) => file !== "gideon-monaco-editor.api.js").map((file) => `monaco/${file}`)) {
      const filePath = path.join(outDir, relativePath);
      await requireFile(filePath, `built trusted web asset ${relativePath}`);
      const content = await readFile(filePath);
      records.push({ path: relativePath, bytes: content.length, sha256: createHash("sha256").update(content).digest("hex") });
    }
    if (generatedFiles.length < records.length) throw new Error("trusted asset output is incomplete");
    await writeFile(path.join(outDir, "manifest.json"), `${JSON.stringify({ version: 1, assets: records }, null, 2)}\n`, "utf8");
    return records;
  } finally {
    await rm(virtualCss, { force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const outputDirectory = parseArguments(process.argv.slice(2));
  try {
    const assets = await buildTrustedWebAssets({ outputDirectory });
    console.log(`Built ${assets.length} trusted console stylesheet, font, and Monaco worker assets in ${outputDirectory}`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
