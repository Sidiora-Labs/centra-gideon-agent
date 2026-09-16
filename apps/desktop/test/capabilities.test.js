const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  CAPABILITIES,
  GRANT_STATES,
  IPC_CHANNELS,
  IPC_PREFIX,
  makeCapabilities,
  registerCapabilityIpc,
} = require("../src/native/capabilities");

const ROOT = path.resolve(__dirname, "..");

function sysPrefsStub({ status = {}, ask = true } = {}) {
  const calls = { get: [], ask: [] };
  return {
    calls,
    getMediaAccessStatus(type) {
      calls.get.push(type);
      if (typeof status === "string") return status;
      return status[type] || "not-determined";
    },
    async askForMediaAccess(type) {
      calls.ask.push(type);
      const granted = typeof ask === "function" ? ask(type) : ask;
      if (typeof status === "object") status[type] = granted ? "granted" : "denied";
      return granted;
    },
  };
}

const mac = (opts = {}) =>
  makeCapabilities({
    platform: "darwin",
    systemPreferences: sysPrefsStub(opts.prefs || {}),
    notification: opts.notification || { isSupported: () => true },
    onChange: opts.onChange,
  });

describe("capability vocabulary", () => {
  it("declares exactly the seven bridge capabilities", () => {
    assert.deepStrictEqual([...CAPABILITIES].sort(), [
      "audio_capture",
      "global_hotkey",
      "login_item",
      "native_notifications",
      "screen_capture",
      "system_audio",
      "tray",
    ]);
  });

  it("declares the five grant states, unavailable included", () => {
    assert.ok(GRANT_STATES.includes("unavailable"));
    assert.strictEqual(GRANT_STATES.length, 5);
  });
});

describe("probe", () => {
  it("reports every capability unavailable off the implemented platform", () => {
    const caps = makeCapabilities({ platform: "linux", systemPreferences: sysPrefsStub() });
    for (const cap of CAPABILITIES) {
      const s = caps.probe(cap);
      assert.strictEqual(s.available, false, cap);
      assert.strictEqual(s.granted, "unavailable", cap);
      assert.strictEqual(s.requestable, false, cap);
      if (cap === "system_audio") continue;
      assert.match(s.reason, /not implemented on linux/, cap);
    }
  });

  it("passes each TCC state through for the microphone", () => {
    for (const state of ["granted", "denied", "restricted", "not-determined"]) {
      const caps = mac({ prefs: { status: { microphone: state } } });
      const s = caps.probe("audio_capture");
      assert.strictEqual(s.granted, state);
      assert.strictEqual(s.available, true);
      assert.strictEqual(s.requestable, state === "not-determined");
    }
  });

  it("treats an unrecognized OS answer as unavailable, not as a grant", () => {
    const caps = mac({ prefs: { status: { microphone: "unknown" } } });
    const s = caps.probe("audio_capture");
    assert.strictEqual(s.available, false);
    assert.strictEqual(s.granted, "unavailable");
  });

  it("marks screen_capture disclosure-only with a reason naming System Settings", () => {
    const caps = mac({ prefs: { status: { screen: "denied" } } });
    const s = caps.probe("screen_capture");
    assert.strictEqual(s.granted, "denied");
    assert.strictEqual(s.requestable, false);
    assert.match(s.reason, /System Settings/);
  });

  it("never claims a notification grant it cannot observe", () => {
    const s = mac().probe("native_notifications");
    assert.strictEqual(s.available, true);
    assert.strictEqual(s.granted, "not-determined");
    assert.strictEqual(s.requestable, false);
    assert.match(s.reason, /does not report notification authorization/);
  });

  it("reports notifications unavailable when the OS does not support them", () => {
    const caps = mac({ notification: { isSupported: () => false } });
    assert.strictEqual(caps.probe("native_notifications").granted, "unavailable");
  });

  it("grants the shell-only capabilities without an OS prompt", () => {
    const caps = mac();
    for (const cap of ["tray", "global_hotkey", "login_item"]) {
      assert.deepStrictEqual(caps.probe(cap), {
        available: true,
        granted: "granted",
        requestable: false,
        reason: "",
      });
    }
    assert.deepStrictEqual(caps.probe("tray").reason, "");
  });

  it("degrades to unavailable when the OS call throws", () => {
    const caps = makeCapabilities({
      platform: "darwin",
      systemPreferences: {
        getMediaAccessStatus() {
          throw new Error("TCC exploded");
        },
      },
    });
    const s = caps.probe("audio_capture");
    assert.strictEqual(s.granted, "unavailable");
    assert.match(s.reason, /probe failed: TCC exploded/);
  });

  it("rejects an unknown capability name", () => {
    assert.strictEqual(mac().probe("keychain").granted, "unavailable");
  });

  it("snapshot covers every capability", () => {
    const snap = mac().snapshot();
    assert.deepStrictEqual(Object.keys(snap).sort(), [...CAPABILITIES].sort());
  });
});

describe("request — the state machine", () => {
  it("not-determined → granted prompts exactly once", async () => {
    const prefs = sysPrefsStub({ status: { microphone: "not-determined" }, ask: true });
    const caps = makeCapabilities({ platform: "darwin", systemPreferences: prefs });
    const res = await caps.request("audio_capture");
    assert.deepStrictEqual(res, { granted: true, state: "granted", prompted: true, reason: "" });
    assert.deepStrictEqual(prefs.calls.ask, ["microphone"]);
  });

  it("not-determined → denied when the user refuses the prompt", async () => {
    const prefs = sysPrefsStub({ status: { microphone: "not-determined" }, ask: false });
    const caps = makeCapabilities({ platform: "darwin", systemPreferences: prefs });
    const res = await caps.request("audio_capture");
    assert.strictEqual(res.granted, false);
    assert.strictEqual(res.state, "denied");
    assert.strictEqual(res.prompted, true);
  });

  it("granted → idempotent, and never re-prompts", async () => {
    const prefs = sysPrefsStub({ status: { microphone: "granted" } });
    const caps = makeCapabilities({ platform: "darwin", systemPreferences: prefs });
    const res = await caps.request("audio_capture");
    assert.deepStrictEqual(res, { granted: true, state: "granted", prompted: false, reason: "" });
    assert.deepStrictEqual(prefs.calls.ask, []);
  });

  for (const state of ["denied", "restricted"]) {
    it(`${state} → no second prompt, and the reason routes to System Settings`, async () => {
      const prefs = sysPrefsStub({ status: { microphone: state } });
      const caps = makeCapabilities({ platform: "darwin", systemPreferences: prefs });
      const res = await caps.request("audio_capture");
      assert.strictEqual(res.granted, false);
      assert.strictEqual(res.state, state);
      assert.strictEqual(res.prompted, false);
      assert.match(res.reason, /System Settings/);
      assert.deepStrictEqual(prefs.calls.ask, []);
    });
  }

  it("unavailable → refuses without touching the OS", async () => {
    const prefs = sysPrefsStub();
    const caps = makeCapabilities({ platform: "linux", systemPreferences: prefs });
    const res = await caps.request("audio_capture");
    assert.strictEqual(res.granted, false);
    assert.strictEqual(res.state, "unavailable");
    assert.strictEqual(res.prompted, false);
    assert.deepStrictEqual(prefs.calls.ask, []);
  });

  it("a disclosure-only capability never prompts", async () => {
    const prefs = sysPrefsStub({ status: { screen: "not-determined" } });
    const caps = makeCapabilities({ platform: "darwin", systemPreferences: prefs });
    const res = await caps.request("screen_capture");
    assert.strictEqual(res.granted, false);
    assert.strictEqual(res.prompted, false);
    assert.match(res.reason, /System Settings/);
    assert.deepStrictEqual(prefs.calls.ask, []);
  });

  it("a failed prompt reports the failure instead of a grant", async () => {
    const caps = makeCapabilities({
      platform: "darwin",
      systemPreferences: {
        getMediaAccessStatus: () => "not-determined",
        askForMediaAccess: async () => {
          throw new Error("no window");
        },
      },
    });
    const res = await caps.request("audio_capture");
    assert.strictEqual(res.granted, false);
    assert.strictEqual(res.prompted, true);
    assert.match(res.reason, /request failed: no window/);
  });

  it("notifies subscribers only when a grant actually changed state", async () => {
    const seen = [];
    const onChange = (cap, state) => seen.push([cap, state.granted]);
    const granting = makeCapabilities({
      platform: "darwin",
      systemPreferences: sysPrefsStub({ status: { microphone: "granted" } }),
      onChange,
    });
    await granting.request("audio_capture");
    assert.deepStrictEqual(seen, []);

    const prompting = makeCapabilities({
      platform: "darwin",
      systemPreferences: sysPrefsStub({ status: { microphone: "not-determined" }, ask: true }),
      onChange,
    });
    await prompting.request("audio_capture");
    assert.deepStrictEqual(seen, [["audio_capture", "granted"]]);

    const again = await prompting.request("audio_capture");
    assert.deepStrictEqual(again, { granted: true, state: "granted", prompted: false, reason: "" });
    assert.strictEqual(seen.length, 1);
  });

  it("rejects an unknown capability name", async () => {
    const res = await mac().request("keychain");
    assert.strictEqual(res.granted, false);
    assert.strictEqual(res.state, "unavailable");
  });
});

describe("IPC surface — the renderer reaches nothing outside the namespace", () => {
  function ipcStub() {
    const handlers = new Map();
    return { handlers, handle: (ch, fn) => handlers.set(ch, fn) };
  }

  it("registers only the gideon-desktop channels", () => {
    const ipc = ipcStub();
    registerCapabilityIpc(ipc, mac());
    const channels = [...ipc.handlers.keys()].sort();
    assert.deepStrictEqual(channels, [IPC_CHANNELS.probe, IPC_CHANNELS.request, IPC_CHANNELS.snapshot].sort());
    for (const ch of channels) assert.ok(ch.startsWith(IPC_PREFIX), ch);
  });

  it("validates the capability argument in the MAIN process, not just the preload", async () => {
    const ipc = ipcStub();
    const caps = mac();
    let probed = 0;
    registerCapabilityIpc(ipc, {
      probe: (c) => (probed++, caps.probe(c)),
      snapshot: () => caps.snapshot(),
      request: (c) => caps.request(c),
    });
    const s = await ipc.handlers.get(IPC_CHANNELS.probe)(null, "../../etc/passwd");
    assert.strictEqual(s.granted, "unavailable");
    assert.strictEqual(probed, 0);
    const r = await ipc.handlers.get(IPC_CHANNELS.request)(null, "keychain");
    assert.strictEqual(r.granted, false);
  });

  it("preload exposes exactly one namespace and no raw electron handles", () => {
    const src = fs.readFileSync(path.join(ROOT, "src/bridge/dashboard-preload.js"), "utf8");
    const exposed = [...src.matchAll(/exposeInMainWorld\(\s*"([^"]+)"/g)].map((m) => m[1]);
    assert.deepStrictEqual(exposed, ["gideonDesktop"]);
    assert.ok(!/exposeInMainWorld\(\s*"electronAPI"/.test(src));
    assert.ok(!/ipcRenderer:\s*ipcRenderer/.test(src), "ipcRenderer must not be handed to the page");
    assert.ok(!/require:/.test(src), "require must not be handed to the page");
  });

  it("preload uses no ipc channel outside IPC_CHANNELS (plus the status feed)", () => {
    const src = fs.readFileSync(path.join(ROOT, "src/bridge/dashboard-preload.js"), "utf8");
    const allowed = new Set([...Object.values(IPC_CHANNELS), "status"]);
    const literals = [...src.matchAll(/ipcRenderer\.\w+\(\s*"([^"]+)"/g)].map((m) => m[1]);
    for (const ch of literals) assert.ok(allowed.has(ch), `unexpected channel: ${ch}`);
    const viaMap = [...src.matchAll(/ipcRenderer\.\w+\(\s*IPC_CHANNELS\.(\w+)/g)].map((m) => m[1]);
    for (const key of viaMap) assert.ok(key in IPC_CHANNELS, `unknown IPC_CHANNELS.${key}`);
    assert.ok(viaMap.length >= 3, "the capability methods must route through IPC_CHANNELS");
  });

  it("the loading screen reads the same single namespace", () => {
    const html = fs.readFileSync(path.join(ROOT, "views/loading.html"), "utf8");
    assert.match(html, /window\.gideonDesktop\?\.onStatus/);
    assert.ok(!html.includes("electronAPI"), "loading.html must not use the retired namespace");
  });
});

describe("contextIsolation stays on", () => {
  const src = fs.readFileSync(path.join(ROOT, "src/application/window-workspace.js"), "utf8");

  it("every window sets contextIsolation: true", () => {
    const flags = [...src.matchAll(/contextIsolation:\s*(\w+)/g)].map((m) => m[1]);
    assert.ok(flags.length >= 2, `expected a flag per window, found ${flags.length}`);
    assert.deepStrictEqual([...new Set(flags)], ["true"]);
  });

  it("every window sets nodeIntegration: false", () => {
    const flags = [...src.matchAll(/nodeIntegration:\s*(\w+)/g)].map((m) => m[1]);
    assert.ok(flags.length >= 2, `expected a flag per window, found ${flags.length}`);
    assert.deepStrictEqual([...new Set(flags)], ["false"]);
  });

  it("declares a webPreferences block for every window it opens", () => {
    const windows = (src.match(/webPreferences:/g) || []).length;
    const isolation = (src.match(/contextIsolation:/g) || []).length;
    assert.strictEqual(isolation, windows, "a webPreferences block without contextIsolation");
  });
});

describe("gateway registration keeps the shell token out of reach", () => {
  const src = fs.readFileSync(path.join(ROOT, "src/application/local-gateway.js"), "utf8");

  it("never writes the token to disk", () => {
    assert.ok(!/writeFileSync\([^)]*shellToken/.test(src));
    assert.ok(!/shellToken[^\n]*writeFile/.test(src));
  });

  it("never logs the token", () => {
    const logged = [...src.matchAll(/(?:console|this\.log)\.\w+\(([^;]*)\)/g)].map((m) => m[1]);
    for (const line of logged) {
      assert.ok(!line.includes("shellToken"), `token reaches a log line: ${line}`);
      assert.ok(!line.includes("localSecret"), `secret reaches a log line: ${line}`);
    }
  });

  it("never sends the token to a renderer", () => {
    const sends = [...src.matchAll(/webContents\?\.send\(([^;]*)\)/g)].map((m) => m[1]);
    for (const line of sends) assert.ok(!line.includes("shellToken"), line);
  });

  it("presents the token only as a request header", () => {
    const uses = [...src.matchAll(/shellToken/g)];
    assert.ok(uses.length >= 4, "expected the token to be declared, set, checked and sent");
    assert.match(src, /"X-Shell-Token": (this\.#shellToken|token)/);
  });
});
