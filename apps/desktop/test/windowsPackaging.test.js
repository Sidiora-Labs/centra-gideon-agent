const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const { mkdtemp, readFile, rm, writeFile } = require("node:fs/promises");
const { existsSync, readFileSync } = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const root = path.resolve(__dirname, "..");
const script = path.join(root, "tooling/build-windows.mjs");
const pkg = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));

function localDependencies(file) {
  const source = readFileSync(path.join(root, file), "utf8");
  return [...source.matchAll(/require\("(\.{1,2}\/[^\"]+)"\)/g)].map((match) => {
    const imported = match[1].endsWith(".js") ? match[1] : `${match[1]}.js`;
    return path.posix.normalize(path.posix.join(path.posix.dirname(file), imported));
  });
}

async function packaging() {
  return import(pathToFileURL(script).href);
}

describe("hosted Windows package configuration", () => {
  it("has a cross-platform Node command and a real Gideon icon", () => {
    assert.equal(pkg.scripts["dist:win"], "node tooling/build-windows.mjs");
    assert.equal(pkg.build.appId, "io.gideon.app");
    assert.equal(pkg.build.productName, "Gideon");
    assert.equal(pkg.build.win.icon, "assets/gideon.ico");
    assert.deepEqual(pkg.build.win.target, [{ target: "nsis", arch: ["x64"] }]);
    const iconPath = path.join(root, pkg.build.win.icon);
    assert.ok(existsSync(iconPath));
    const icon = readFileSync(iconPath);
    assert.equal(icon.readUInt16LE(0), 0);
    assert.equal(icon.readUInt16LE(2), 1);
    assert.ok(icon.readUInt16LE(4) >= 4, "icon needs multiple Windows resolutions");
    const sizes = new Set();
    for (let i = 0; i < icon.readUInt16LE(4); i += 1) {
      sizes.add(icon[i * 16 + 6] || 256);
    }
    assert.ok(sizes.has(16));
    assert.ok(sizes.has(32));
    assert.ok(sizes.has(256));
  });

  it("uses the confirmed production origin and accepts explicit HTTPS overrides", async () => {
    const { hostedOrigin, configuredOrigin, DEFAULT_HOSTED_ORIGIN } = await packaging();
    assert.equal(DEFAULT_HOSTED_ORIGIN, "https://gideon.workmates.app");
    assert.equal(configuredOrigin(), DEFAULT_HOSTED_ORIGIN);
    assert.equal(configuredOrigin("https://alternate.example"), "https://alternate.example");
    assert.equal(hostedOrigin("https://gideon.example"), "https://gideon.example");
    assert.equal(hostedOrigin("https://GIDEON.example:443/"), "https://gideon.example");
    assert.equal(hostedOrigin("https://gideon.example:8443"), "https://gideon.example:8443");
    assert.equal(hostedOrigin("https://[2001:db8::1]:8443"), "https://[2001:db8::1]:8443");
  });

  it("rejects missing, malformed and non-origin configuration before building", async () => {
    const { hostedOrigin } = await packaging();
    for (const invalid of [undefined, "", "gideon.example", "http://gideon.example",
      "https://gideon.example/sign-in", "https://gideon.example/?route=chat",
      "https://gideon.example/#chat", "https://user@gideon.example",
      "https://user:secret@gideon.example", "https://gideon.example/../admin",
      "https://gideon.example\n", "https://", "https://gideon.example:bad-port"]) {
      assert.throws(() => hostedOrigin(invalid), /GIDEON_CLOUD_URL/, String(invalid));
    }
  });

  it("fails the command clearly for an invalid operator override", () => {
    const env = { ...process.env, GIDEON_CLOUD_URL: "http://insecure.example" };
    const result = spawnSync(process.execPath, [script], { cwd: root, env, encoding: "utf8" });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /GIDEON_CLOUD_URL must be an explicit HTTPS origin/);
    assert.doesNotMatch(result.stderr, /Cannot find package/);
  });

  it("overrides backend resources for Windows and keeps Mac/Linux settings unchanged", async () => {
    const { windowsConfig } = await packaging();
    const baseline = structuredClone(pkg.build);
    const resource = path.join(os.tmpdir(), "hosted-config.json");
    const config = windowsConfig(pkg.build, resource);
    assert.deepEqual(pkg.build, baseline, "building Windows must not mutate shared configuration");
    assert.deepEqual(config.mac, baseline.mac);
    assert.deepEqual(config.linux, baseline.linux);
    assert.deepEqual(config.dmg, baseline.dmg);
    assert.equal(pkg.build.extraResources, undefined);
    assert.deepEqual(baseline.mac.extraResources, baseline.linux.extraResources);
    assert.equal(baseline.mac.extraResources[0].from, "backend-dist/gideon-backend");
    assert.deepEqual(config.extraResources, [{ from: resource, to: "hosted-config.json" }]);
    assert.ok(!config.extraResources.some((entry) => entry.from.includes("backend-dist")));
    assert.ok(config.files.includes("src/connection/hosted-config.js"));
    assert.deepEqual(config.files, baseline.files);
    assert.ok(config.files.includes(pkg.main));
    assert.equal(config.win.icon, "assets/gideon.ico");
    assert.deepEqual(config.win.target, [{ target: "nsis", arch: ["x64"] }]);
    assert.equal(config.nsis.artifactName, "Gideon-Setup-${version}-${arch}.${ext}");
    assert.equal(config.directories.output, "dist");
    assert.equal(config.publish, null);
  });

  it("bundles all transitive application modules for every platform", () => {
    assert.ok(pkg.build.files.includes("src/connection/hosted-config.js"));
    const seen = new Set();
    const pending = [pkg.main];
    while (pending.length) {
      const file = pending.shift();
      if (seen.has(file)) continue;
      seen.add(file);
      assert.ok(pkg.build.files.includes(file), `Missing base bundle entry: ${file}`);
      assert.ok(existsSync(path.join(root, file)), `Missing source module: ${file}`);
      pending.push(...localDependencies(file));
    }
    assert.ok(seen.has("src/application/desktop-application.js"));
  });

  it("requires the existing Gideon identity before making a Windows override", async () => {
    const { windowsConfig } = await packaging();
    assert.throws(() => windowsConfig({ ...pkg.build, appId: "other.app" }, "/tmp/config"));
    assert.throws(() => windowsConfig({ ...pkg.build, productName: "Other" }, "/tmp/config"));
  });
});

describe("Windows checksum manifest", () => {
  it("hashes the actual installer bytes and names the artifact", async () => {
    const { checksumManifest } = await packaging();
    const dir = await mkdtemp(path.join(os.tmpdir(), "gideon-checksum-test-"));
    try {
      const installer = path.join(dir, "Gideon-Setup-0.1.3-x64.exe");
      const bytes = Buffer.from("installer bytes for hash verification\n");
      await writeFile(installer, bytes);
      const result = await checksumManifest([installer], dir);
      assert.equal(result.installer, installer);
      assert.equal(result.manifest, path.join(dir, "SHA256SUMS.txt"));
      const expected = createHash("sha256").update(bytes).digest("hex");
      assert.equal(await readFile(result.manifest, "utf8"),
        `${expected}  Gideon-Setup-0.1.3-x64.exe\n`);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it("refuses missing and ambiguous installers", async () => {
    const { checksumManifest } = await packaging();
    await assert.rejects(checksumManifest([], os.tmpdir()), /Expected one Windows NSIS installer, received 0/);
    await assert.rejects(checksumManifest(["a.exe", "b.exe"], os.tmpdir()),
      /Expected one Windows NSIS installer, received 2/);
  });
});
