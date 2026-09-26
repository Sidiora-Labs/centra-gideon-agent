"use strict";

const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { test } = require("node:test");

const root = path.resolve(__dirname, "../../..");
const workflow = fs.readFileSync(path.join(root, ".github/workflows/gu26-desktop.yml"), "utf8");
const smokeSource = fs.readFileSync(path.join(__dirname, "electron/windowsHostedSmoke.js"), "utf8");
const artifactSource = fs.readFileSync(path.join(__dirname, "../tooling/verify-windows-artifact.mjs"), "utf8");
const artifact = import(pathToFileURL(path.join(__dirname, "../tooling/verify-windows-artifact.mjs")));
const smoke = require("./electron/windowsHostedSmoke");

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "gideon-win-proof-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const install = path.join(directory, "install");
  const resources = path.join(install, "resources");
  const dist = path.join(directory, "dist");
  fs.mkdirSync(resources, { recursive: true });
  fs.mkdirSync(dist);
  fs.writeFileSync(path.join(install, "Gideon.exe"), "MZ");
  fs.writeFileSync(path.join(install, "Uninstall Gideon.exe"), "MZ");
  fs.writeFileSync(path.join(resources, "app.asar"), "packaged application");
  fs.writeFileSync(path.join(resources, "hosted-config.json"), JSON.stringify({
    schemaVersion: 1, mode: "hosted", hostedUrl: "https://approved.example",
  }));
  return { directory, install, resources, dist };
}

test("workflow is manually dispatched on Windows with read-only repository permission", () => {
  assert.match(workflow, /on:\s*\n\s*workflow_dispatch:/);
  assert.match(workflow, /permissions:\s*\n\s*contents: read/);
  assert.match(workflow, /runs-on: windows-latest/);
  assert.doesNotMatch(workflow, /(?:push:|release:|deploy|create-release|publish|contents: write)/i);
});

test("workflow requires operator URL and a provisioned hosted identity before building", () => {
  assert.match(workflow, /GIDEON_CLOUD_URL: \$\{\{ vars\.GIDEON_CLOUD_URL \|\| 'https:\/\/gideon\.centra\.ag' \}\}/);
  assert.match(workflow, /GIDEON_SMOKE_EMAIL: \$\{\{ secrets\.GIDEON_SMOKE_EMAIL \}\}/);
  assert.match(workflow, /GIDEON_SMOKE_PASSWORD: \$\{\{ secrets\.GIDEON_SMOKE_PASSWORD \}\}/);
  const required = workflow.indexOf("Require approved hosted configuration");
  const build = workflow.indexOf("npm run --workspace apps/desktop dist:win");
  assert.ok(required >= 0 && build > required);
  assert.match(workflow, /if \(-not \$env:GIDEON_CLOUD_URL\).*throw/);
  assert.match(workflow, /Scheme -ne 'https'/);
  assert.match(workflow, /AbsoluteUri -ne/);
  assert.match(workflow, /if \(-not \$env:GIDEON_SMOKE_EMAIL -or -not \$env:GIDEON_SMOKE_PASSWORD\)/);
  assert.match(workflow, /https:\/\/gideon\.centra\.ag/);
});

test("workflow installs locked dependencies and builds the real x64 NSIS target", () => {
  assert.match(workflow, /npm ci/);
  assert.doesNotMatch(workflow, /pnpm\/action-setup@v4/);
  assert.match(workflow, /npm run --workspace apps\/desktop dist:win/);
  assert.match(workflow, /Gideon-Setup-\*-x64\.exe/);
  assert.match(workflow, /Start-Process -FilePath \$installer\.FullName/);
  assert.match(workflow, /ArgumentList @\('\/S', "\/D=\$install"\)/);
  assert.match(workflow, /if \(\$process.ExitCode -ne 0\)/);
  assert.match(workflow, /Test-Path \(Join-Path \$install 'Gideon\.exe'\)/);
});

test("workflow orders package inspection, installed app smoke, uninstall and proof upload", () => {
  const stages = [
    "Build hosted Windows x64 NSIS installer",
    "Install the actual NSIS artifact",
    "verify-windows-artifact.mjs",
    "windowsHostedSmoke.js",
    "Uninstall and record native result",
    "actions/upload-artifact@v4",
  ];
  const positions = stages.map((stage) => workflow.indexOf(stage));
  assert.ok(positions.every((position) => position >= 0));
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
  assert.match(workflow, /if \(Test-Path \(Join-Path \$env:GIDEON_INSTALL_DIR 'Gideon\.exe'\)\).*throw/);
  assert.match(workflow, /Add-Member -NotePropertyName uninstall -NotePropertyValue 'passed'/);
  assert.match(workflow, /windows-proof\.json/);
  assert.match(workflow, /SHA256SUMS\.txt/);
  assert.match(workflow, /if: always\(\)/);
});

test("approvedOrigin accepts only a bare HTTPS origin", async () => {
  const { approvedOrigin } = await artifact;
  assert.equal(approvedOrigin("https://approved.example"), "https://approved.example");
  assert.equal(approvedOrigin("https://approved.example/"), "https://approved.example");
  assert.equal(approvedOrigin("https://approved.example:8443"), "https://approved.example:8443");
  for (const bad of ["", "http://approved.example", "https://approved.example/path",
    "https://approved.example/?next=files", "https://approved.example/#/projects",
    "https://user:secret@approved.example", "file:///tmp/hosted.html"]) {
    assert.throws(() => approvedOrigin(bad), undefined, bad);
  }
});

test("inspectInstall reads the installed config and requires executable, uninstaller and asar", async (t) => {
  const { inspectInstall } = await artifact;
  const files = fixture(t);
  const observed = inspectInstall(files.install, "https://approved.example");
  assert.deepEqual(observed.config, { schemaVersion: 1, mode: "hosted", hostedUrl: "https://approved.example" });
  assert.equal(observed.executable, path.join(files.install, "Gideon.exe"));
  assert.equal(observed.uninstaller, path.join(files.install, "Uninstall Gideon.exe"));
  for (const missing of ["Gideon.exe", "Uninstall Gideon.exe", "resources/app.asar",
    "resources/hosted-config.json"]) {
    const target = path.join(files.install, missing);
    const content = fs.readFileSync(target);
    fs.rmSync(target);
    assert.throws(() => inspectInstall(files.install, "https://approved.example"), undefined, missing);
    fs.writeFileSync(target, content);
  }
});

test("inspectInstall rejects wrong config, a different origin and bundled local backend", async (t) => {
  const { inspectInstall } = await artifact;
  const files = fixture(t);
  const configFile = path.join(files.resources, "hosted-config.json");
  assert.throws(() => inspectInstall(files.install, "https://other.example"));
  for (const change of [{ mode: "local" }, { schemaVersion: 2 }, { hostedUrl: "http://approved.example" }]) {
    fs.writeFileSync(configFile, JSON.stringify({ schemaVersion: 1, mode: "hosted",
      hostedUrl: "https://approved.example", ...change }));
    assert.throws(() => inspectInstall(files.install, "https://approved.example"));
  }
  fs.writeFileSync(configFile, JSON.stringify({ schemaVersion: 1, mode: "hosted",
    hostedUrl: "https://approved.example" }));
  fs.mkdirSync(path.join(files.resources, "backend-dist"));
  assert.throws(() => inspectInstall(files.install, "https://approved.example"), /local backend/);
});

test("inspectInstall refuses malformed or expanded packaged configuration", async (t) => {
  const { inspectInstall } = await artifact;
  const files = fixture(t);
  const configFile = path.join(files.resources, "hosted-config.json");
  fs.writeFileSync(configFile, "{broken");
  assert.throws(() => inspectInstall(files.install, "https://approved.example"), SyntaxError);
  fs.writeFileSync(configFile, JSON.stringify({ schemaVersion: 1, mode: "hosted",
    hostedUrl: "https://approved.example", localBackend: true }));
  assert.throws(() => inspectInstall(files.install, "https://approved.example"));
  fs.writeFileSync(configFile, JSON.stringify({ schemaVersion: 1, mode: "hosted",
    hostedUrl: "https://approved.example/" }));
  assert.throws(() => inspectInstall(files.install, "https://approved.example"));
});

test("findInstaller requires exactly one matching x64 setup, and sha256 hashes its bytes", async (t) => {
  const { findInstaller, sha256 } = await artifact;
  const { dist } = fixture(t);
  assert.throws(() => findInstaller(dist), /expected one/);
  fs.writeFileSync(path.join(dist, "Gideon-Setup-0.1.3-arm64.exe"), "MZarm");
  assert.throws(() => findInstaller(dist), /expected one/);
  const chosen = path.join(dist, "Gideon-Setup-0.1.3-x64.exe");
  fs.writeFileSync(chosen, Buffer.from([0x4d, 0x5a, 0x00, 0xff]));
  assert.equal(findInstaller(dist), chosen);
  assert.equal(await sha256(chosen), createHash("sha256").update(fs.readFileSync(chosen)).digest("hex"));
  fs.writeFileSync(path.join(dist, "Gideon-Setup-0.1.4-x64.exe"), "MZnext");
  assert.throws(() => findInstaller(dist), /expected one/);
});

test("native-only verifier refuses Linux success and checks the build manifest and signature", () => {
  assert.match(artifactSource, /process\.platform, 'win32'/);
  assert.match(artifactSource, /buildManifest.*digest/);
  assert.match(artifactSource, /Get-AuthenticodeSignature/);
  assert.match(artifactSource, /hosted-config\.json/);
  assert.match(artifactSource, /backend-dist/);
  assert.match(artifactSource, /SHA256SUMS\.txt/);
});

test("artifact verifier CLI cannot report native installation proof on Linux", {
  skip: process.platform === "win32",
}, () => {
  const result = spawnSync(process.execPath,
    [path.join(__dirname, "../tooling/verify-windows-artifact.mjs"), "dist", "install",
      "https://approved.example", "proof"], { encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /artifact installation proof requires a Windows runner/);
});

test("smoke polling resolves after a real asynchronous state change and times out honestly", async () => {
  let ready = false;
  setTimeout(() => { ready = true; }, 20);
  assert.equal(await smoke.until("ready", () => ready, 1000), true);
  await assert.rejects(smoke.until("never", () => false, 20), /Timed out waiting for never/);
});

test("smoke can discover a real loopback HTTP CDP target and ignores unrelated targets", async () => {
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify([
      { type: "page", url: "file:///loading.html", webSocketDebuggerUrl: "ws://127.0.0.1:9/loading" },
      { type: "page", url: "https://other.example", webSocketDebuggerUrl: "ws://127.0.0.1:9/other" },
      { type: "page", url: "https://approved.example/#/projects", webSocketDebuggerUrl: "ws://127.0.0.1:9/hosted" },
    ]));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const target = await smoke.hostedTarget(server.address().port, "https://approved.example");
    assert.equal(target.url, "https://approved.example/#/projects");
    assert.equal(await smoke.hostedTarget(server.address().port, "https://missing.example"), undefined);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("smoke allocates a loopback port that can be rebound", async () => {
  const port = await smoke.freePort();
  const server = http.createServer();
  await new Promise((resolve) => server.listen(port, "127.0.0.1", resolve));
  assert.equal(server.address().port, port);
  await new Promise((resolve) => server.close(resolve));
});

test("native smoke checks return, navigation, reopening and remote privilege boundary", () => {
  assert.ok(smokeSource.includes('input[name="email"]'));
  assert.match(smokeSource, /location\.hash = '#\/projects'/);
  assert.match(smokeSource, /sign-in return to Projects/);
  assert.match(smokeSource, /button\[aria-label="Files"\]/);
  assert.match(smokeSource, /button\.click\(\)/);
  assert.match(smokeSource, /hosted Files navigation/);
  assert.match(smokeSource, /await stop\(app\);\s*app = await launch\(executable, profile, origin\)/);
  const reopen = smokeSource.split('until("reopened signed-in hosted page"')[1].split('const reopenedUrl')[0];
  assert.match(reopen, /!document\.querySelector\('input\[name="email"\]'\)/);
  assert.match(reopen, /\.gideon-shell nav\[data-tour="rail"\]/);
  assert.match(reopen, /\.gideon-workspace/);
  assert.doesNotMatch(reopen, /innerText\.includes\('Projects'\)/);
  assert.match(smokeSource, /privilegedRemoteBridge: "absent"/);
  assert.match(smokeSource, /state\.bridge, "undefined"/);
  assert.match(smokeSource, /state\.requireType, "undefined"/);
  assert.match(smokeSource, /state\.processType, "undefined"/);
  assert.match(smokeSource, /GIDEON_CLOUD_URL: ""/);
});
