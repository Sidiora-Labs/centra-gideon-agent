const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");

const LOCAL_REQUIRE = /require\("(\.{1,2}\/[^\"]+)"\)/g;

function dependencies(file) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  return [...source.matchAll(LOCAL_REQUIRE)].map((match) => {
    const relative = match[1].endsWith(".js") ? match[1] : `${match[1]}.js`;
    return path.posix.normalize(path.posix.join(path.posix.dirname(file), relative));
  });
}

describe("electron-builder files list", () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  const bundledFiles = pkg.build.files;

  it("includes every local dependency from the application entry point", () => {
    const missing = dependencies(pkg.main).filter((file) => !bundledFiles.includes(file));
    assert.deepStrictEqual(missing, [], `Missing from build.files: ${missing.join(", ")}`);
  });

  it("includes all transitively required application modules", () => {
    const seen = new Set();
    const queue = [pkg.main];
    const missing = [];
    while (queue.length) {
      const file = queue.shift();
      if (seen.has(file)) continue;
      seen.add(file);
      for (const dependency of dependencies(file)) {
        if (!bundledFiles.includes(dependency)) missing.push(`${file} → ${dependency}`);
        queue.push(dependency);
      }
    }
    assert.deepStrictEqual(missing, [], `Missing from build.files: ${missing.join(", ")}`);
  });

  it("includes every preload, view and asset loaded relative to a module", () => {
    const missing = [];
    for (const file of bundledFiles.filter((entry) => entry.endsWith(".js"))) {
      const source = fs.readFileSync(path.join(ROOT, file), "utf8");
      for (const match of source.matchAll(/path\.join\(__dirname,\s*"([^\"]+)"\)/g)) {
        const dependency = path.posix.normalize(path.posix.join(path.posix.dirname(file), match[1]));
        if (!fs.existsSync(path.join(ROOT, dependency))) missing.push(`${file} → ${dependency} (missing)`);
        else if (!bundledFiles.includes(dependency)) missing.push(`${file} → ${dependency}`);
      }
    }
    assert.deepStrictEqual(missing, [], `Missing from build.files: ${missing.join(", ")}`);
  });

  it("keeps the source scanners non-vacuous after module moves", () => {
    assert.deepStrictEqual(dependencies(pkg.main), ["src/application/desktop-application.js"]);
    assert.ok(dependencies("src/application/desktop-application.js").length >= 5);
    const dialog = fs.readFileSync(path.join(ROOT, "src/connection/dialog.js"), "utf8");
    assert.ok([...dialog.matchAll(/path\.join\(__dirname,\s*"([^\"]+)"\)/g)].length >= 2);
  });

  it("does not reference files that no longer exist", () => {
    const stale = bundledFiles.filter((file) => !fs.existsSync(path.join(ROOT, file)));
    assert.deepStrictEqual(stale, [], `Stale entries in build.files: ${stale.join(", ")}`);
  });
});

describe("electron-builder Linux target (DC-6)", () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  const linux = pkg.build.linux;

  it("declares exactly the AppImage + deb targets the ruling shipped", () => {
    assert.deepStrictEqual(linux.target, ["AppImage", "deb"]);
  });

  it("points its icon at a file that exists", () => {
    assert.ok(linux.icon, "linux.icon missing");
    assert.ok(fs.existsSync(path.join(ROOT, linux.icon)), `linux icon not found: ${linux.icon}`);
  });

  it("carries the maintainer contact the deb target requires", () => {
    assert.match(linux.maintainer || "", /<[^@\s]+@[^@\s]+>/,
      "linux.maintainer must be 'Name <email>' — the deb control file requires it");
  });

  it("has a dist:linux script that builds --linux with the pinned electron version", () => {
    const script = pkg.scripts["dist:linux"];
    assert.ok(script, "scripts['dist:linux'] missing");
    assert.match(script, /--linux/);
    assert.match(script, /--config\.electronVersion=/);
  });

  it("does not carry a mac-only package name into the deb Package field", () => {
    assert.strictEqual(pkg.name, "gideon-desktop");
  });
});
