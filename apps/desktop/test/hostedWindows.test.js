"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const https = require("node:https");
const { execFile } = require("node:child_process");
const { isHostedWindowsMode, validateHostedOrigin, readHostedConfig } = require("../src/connection/hosted-config");
const { configureHostedEndpoint, hostedGatewayUrl, withHostedEndpoint, HOSTED_ENDPOINT_ID } = require("../src/connection/hosted");
const { EndpointSession } = require("../src/application/endpoint-session");
const { loadRegistry, saveRegistry } = require("../src/storage/endpoint-registry");
const { writeConfirmation } = require("../src/connection/controller");

function fixture(url = "https://cloud.example.test") {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "gideon-hosted-windows-"));
  const resourcesPath = path.join(root, "resources");
  const home = path.join(root, "home");
  fs.mkdirSync(resourcesPath);
  fs.writeFileSync(path.join(resourcesPath, "hosted-config.json"),
    JSON.stringify({ schemaVersion: 1, mode: "hosted", hostedUrl: url }));
  return { root, resourcesPath, home, clean: () => fs.rmSync(root, { recursive: true, force: true }) };
}

function sessionAt(fixture, { dialogResponse = 1 } = {}) {
  const events = { mounts: [], messages: [], loaded: [], quitting: 0, opened: 0 };
  const page = {
    loadFile: (file) => events.loaded.push(file),
    loadURL: (url) => events.loaded.push(url),
    once: () => {},
  };
  const window = { isDestroyed: () => false, webContents: page };
  const electron = {
    app: { isPackaged: true },
    BrowserWindow: class {},
    ipcMain: { handle: () => {} },
    dialog: { showMessageBox: async (_window, message) => {
      events.messages.push(message);
      return { response: typeof dialogResponse === "function" ? dialogResponse() : dialogResponse };
    } },
  };
  const workspace = {
    hasBridge: () => true,
    mount: (_window, options) => { events.mounts.push(options); },
  };
  const session = new EndpointSession({ gateway: { url: "", waitReady: () => { throw new Error("local gateway started"); } },
    workspace, mainWindow: () => window, home: fixture.home, electron,
    hostedConfigOptions: { platform: "win32", resourcesPath: fixture.resourcesPath },
    quit: () => { events.quitting++; } });
  session.initialize();
  return { session, events, window };
}

test("only packaged Windows enters hosted mode", () => {
  assert.equal(isHostedWindowsMode({ platform: "win32", isPackaged: true }), true);
  for (const options of [{ platform: "win32", isPackaged: false }, { platform: "darwin", isPackaged: true },
    { platform: "linux", isPackaged: true }]) assert.equal(isHostedWindowsMode(options), false);
  assert.equal(readHostedConfig({ platform: "linux", isPackaged: true, resourcesPath: "/missing" }), null);
  assert.equal(readHostedConfig({ platform: "win32", isPackaged: false, resourcesPath: "/missing" }), null);
});

test("configuration requires an explicit HTTPS origin and exact resource schema", () => {
  assert.equal(validateHostedOrigin("https://gideon.example:8443"), "https://gideon.example:8443");
  for (const url of ["", " https://gideon.example", "http://gideon.example", "https://gideon.example/",
    "https://gideon.example/path", "https://gideon.example?next=/chat", "https://gideon.example/#/chat",
    "https://user:pass@gideon.example", "https://gideon.example:443", "javascript:alert(1)", "https://", 12]) {
    assert.throws(() => validateHostedOrigin(url), /HTTPS origin/);
  }
  assert.throws(() => readHostedConfig({ platform: "win32", isPackaged: true, resourcesPath: "" }), /resources path/);
  const f = fixture();
  try {
    const options = { platform: "win32", isPackaged: true, resourcesPath: f.resourcesPath };
    assert.deepEqual(readHostedConfig(options), { schemaVersion: 1, mode: "hosted", hostedUrl: "https://cloud.example.test" });
    fs.writeFileSync(path.join(f.resourcesPath, "hosted-config.json"), "{");
    assert.throws(() => readHostedConfig(options), /could not be read.*hosted-config\.json/);
    for (const bad of [null, [], { schemaVersion: 2, mode: "hosted", hostedUrl: "https://cloud.example.test" },
      { schemaVersion: 1, mode: "local", hostedUrl: "https://cloud.example.test" },
      { schemaVersion: 1, mode: "hosted", hostedUrl: "http://cloud.example.test" }]) {
      fs.writeFileSync(path.join(f.resourcesPath, "hosted-config.json"), JSON.stringify(bad));
      assert.throws(() => readHostedConfig(options), /configuration is invalid/);
    }
    fs.rmSync(path.join(f.resourcesPath, "hosted-config.json"));
    assert.throws(() => readHostedConfig(options), /could not be read.*hosted-config\.json/);
  } finally { f.clean(); }
});

test("packaged configuration wins over process environment without erasing other gateways", () => {
  const f = fixture("https://cloud.example.test");
  try {
    const { session } = sessionAt(f);
    assert.equal(hostedGatewayUrl({ GIDEON_CLOUD_URL: "https://wrong.example" }), "https://cloud.example.test");
    assert.equal(session.target(), "https://cloud.example.test");
    const saved = session.registry();
    assert.equal(saved.active, HOSTED_ENDPOINT_ID);
    assert.equal(saved.endpoints.find((row) => row.id === HOSTED_ENDPOINT_ID).base_url, "https://cloud.example.test");
    const another = { id: "ep_other", label: "Other", base_url: "https://1.1.1.1", kind: "remote", device_session_ref: "" };
    saveRegistry(session.state.store, { active: another.id, endpoints: [...saved.endpoints, another] });
    writeConfirmation(session.state.store, another.id, { origin: another.base_url, scheme: "https", trust: "public", addresses: "" });
    const restarted = sessionAt(f).session;
    assert.equal(restarted.registry().active, another.id);
    assert.equal(restarted.registry().endpoints.length, 2);
    assert.equal(restarted.registry().endpoints.find((row) => row.id === HOSTED_ENDPOINT_ID).base_url, "https://cloud.example.test");
  } finally { configureHostedEndpoint(null); f.clean(); }
});

test("hosted startup keeps a confirmed remote selection and falls back from a local row", async () => {
  const f = fixture();
  try {
    const { session } = sessionAt(f);
    const other = { id: "ep_other", label: "Other", base_url: "https://1.1.1.1", kind: "remote", device_session_ref: "" };
    saveRegistry(session.state.store, { active: other.id, endpoints: [...session.registry().endpoints, other] });
    writeConfirmation(session.state.store, other.id, { origin: other.base_url, scheme: "https", trust: "public", addresses: "" });
    assert.equal(await session.chooseHostedStartup(), other.base_url);
    assert.equal(session.registry().active, other.id);
    const local = { ...other, id: "ep_local", kind: "local", base_url: "http://localhost:10000" };
    saveRegistry(session.state.store, { active: local.id, endpoints: [...session.registry().endpoints, local] });
    assert.equal(await session.chooseHostedStartup(), "https://cloud.example.test");
    assert.equal(session.registry().active, HOSTED_ENDPOINT_ID);
    assert.equal(session.registry().endpoints.length, 3);
    assert.equal(loadRegistry(session.state.store).endpoints.find((row) => row.id === "ep_local").base_url, local.base_url);
  } finally { configureHostedEndpoint(null); f.clean(); }
});

test("a refused hosted connection has a recoverable dialog and never mounts a privileged bridge", async () => {
  const f = fixture("https://0.0.0.0");
  try {
    const { session, events, window } = sessionAt(f);
    session.openDialog = () => { events.opened++; };
    assert.equal(await session.navigate(window, "https://0.0.0.0"), false);
    assert.deepEqual(events.mounts, [{ attachBridge: false }]);
    assert.equal(events.messages[0].title, "Gideon");
    assert.deepEqual(events.messages[0].buttons, ["Retry", "Gateways", "Quit"]);
    assert.equal(events.opened, 1);
    assert.equal(events.quitting, 0);
  } finally { configureHostedEndpoint(null); f.clean(); }
});

test("Mac/Linux sessions retain environment configuration and existing local startup contract", () => {
  configureHostedEndpoint(null);
  assert.equal(hostedGatewayUrl({ GIDEON_CLOUD_URL: "https://existing.example/path" }), "https://existing.example");
  assert.equal(hostedGatewayUrl({ GIDEON_CLOUD_URL: "http://insecure.example" }), "");
  assert.equal(hostedGatewayUrl({ GIDEON_CLOUD_URL: "https://" }), "");
  const row = { id: "ep_owned", label: "Owned", base_url: "http://localhost:4321", kind: "local", device_session_ref: "" };
  const registry = withHostedEndpoint({ active: row.id, endpoints: [row] }, {});
  assert.deepEqual(registry, { active: row.id, endpoints: [row] });
});

test("the OSS desktop probes the hosted central health route over HTTPS", async () => {
  const fixtureDir = path.join(__dirname, "fixtures");
  const key = fs.readFileSync(path.join(fixtureDir, "hosted-key.pem"));
  const cert = fs.readFileSync(path.join(fixtureDir, "hosted-cert.pem"));
  const requests = [];
  const server = https.createServer({ key, cert }, (request, response) => {
    requests.push(request.url);
    response.writeHead(request.url === "/gideon/v1/healthz" ? 200 : 404,
      { "Content-Type": "application/json" });
    response.end(JSON.stringify({ status: "ok", role: "central" }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const origin = `https://127.0.0.1:${server.address().port}`;
    const script = `const { probeEndpoint } = require(${JSON.stringify(path.join(__dirname, "../src/connection/controller.js"))});
      probeEndpoint(process.env.GIDEON_CLOUD_URL).then(result => process.stdout.write(JSON.stringify(result)))`;
    const result = await new Promise((resolve, reject) => execFile(process.execPath, ["-e", script], {
      env: { ...process.env, GIDEON_CLOUD_URL: origin,
        NODE_EXTRA_CA_CERTS: path.join(fixtureDir, "hosted-cert.pem") },
    }, (error, stdout, stderr) => error ? reject(new Error(stderr || error.message)) : resolve(JSON.parse(stdout))));
    assert.equal(result.status, "reachable");
    assert.equal(result.version, "hosted");
    assert.deepEqual(requests, ["/gideon/v1/healthz"]);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
