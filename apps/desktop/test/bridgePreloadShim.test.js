"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { CAPABILITIES, IPC_CHANNELS } = require("../src/native/capabilities");

const filename = path.join(__dirname, "../src/bridge/dashboard-preload.js");
const source = fs.readFileSync(filename, "utf8");

function loadSandboxedPreload() {
  const exposed = new Map();
  const imports = [];
  const events = new EventEmitter();
  const context = vm.createContext({
    require(name) {
      imports.push(name);
      if (name !== "electron") throw new Error(`sandbox require denied: ${name}`);
      return {
        contextBridge: { exposeInMainWorld: exposed.set.bind(exposed) },
        ipcRenderer: events,
      };
    },
  });
  vm.runInContext(source, context, { filename });
  return { exposed, imports, events, context, bridge: exposed.get("gideonDesktop") };
}

test("restricted require can initialize the complete bridge without local modules", () => {
  const { exposed, imports, bridge } = loadSandboxedPreload();
  assert.deepEqual(imports, ["electron"]);
  assert.deepEqual([...exposed.keys()], ["gideonDesktop"]);
  assert.deepEqual(Object.keys(bridge).sort(), ["capabilities", "loginItem", "notifications", "onStatus", "pushToTalk"]);
  assert.equal("ipcRenderer" in bridge, false);
  assert.equal("require" in bridge, false);
});

test("inline capability and channel constants match their main-process owners", () => {
  const { bridge, context } = loadSandboxedPreload();
  assert.deepEqual(Array.from(bridge.capabilities.names()), CAPABILITIES);
  assert.deepEqual(JSON.parse(vm.runInContext("JSON.stringify(IPC_CHANNELS)", context)), IPC_CHANNELS);
});

test("the loaded bridge forwards payloads and unregisters event listeners", () => {
  const { bridge, events } = loadSandboxedPreload();
  const received = [];
  const stop = bridge.onStatus((payload) => received.push(payload));
  const payload = { message: "gateway ready" };
  events.emit("status", { sender: "private event" }, payload);
  assert.deepEqual(received, [payload]);
  stop();
  assert.equal(events.listenerCount("status"), 0);
  events.emit("status", {}, "later");
  assert.deepEqual(received, [payload]);
});

test("capability subscriptions filter state without leaking the IPC event", () => {
  const { bridge, events } = loadSandboxedPreload();
  const received = [];
  const stop = bridge.capabilities.on("audio_capture", (state) => received.push(state));
  events.emit(IPC_CHANNELS.state, {}, { capability: "tray", state: "other" });
  events.emit(IPC_CHANNELS.state, {}, { capability: "audio_capture", state: "granted" });
  assert.deepEqual(received, ["granted"]);
  stop();
  assert.equal(events.listenerCount(IPC_CHANNELS.state), 0);
});

test("unknown capabilities are refused before any IPC invocation", async () => {
  const { bridge } = loadSandboxedPreload();
  assert.equal((await bridge.capabilities.probe("unknown")).granted, "unavailable");
  assert.equal((await bridge.capabilities.request("unknown")).prompted, false);
});
