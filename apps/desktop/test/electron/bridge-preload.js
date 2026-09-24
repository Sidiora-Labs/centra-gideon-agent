"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const electron = require("electron");
const { app, ipcMain, BaseWindow } = electron;
const { WindowWorkspace } = require("../../src/application/window-workspace");
const { CAPABILITIES, makeCapabilities, registerCapabilityIpc } = require("../../src/native/capabilities");

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "gideon-preload-"));
app.setPath("userData", profile);
const timeout = setTimeout(() => {
  console.error("Sandboxed preload integration timed out");
  app.exit(1);
}, 30000);
let window;
let workspace;

async function run() {
  assert.equal(app.commandLine.hasSwitch("no-sandbox"), false, "This tier requires Chromium sandboxing");
  assert.notEqual(process.env.ELECTRON_DISABLE_SANDBOX, "1");
  await app.whenReady();
  registerCapabilityIpc(ipcMain, makeCapabilities({
    platform: process.platform,
    systemPreferences: electron.systemPreferences,
    notification: electron.Notification,
  }));
  workspace = new WindowWorkspace(electron, { target: () => "", local: () => "" });
  window = new BaseWindow({ show: false, width: 640, height: 480 });
  const page = workspace.mount(window, { attachBridge: true });
  const failures = [];
  page.on("preload-error", (_event, preloadPath, error) => failures.push(`${preloadPath}: ${error.message}`));
  const preferences = page.getLastWebPreferences();
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.nodeIntegration, false);
  assert.equal(workspace.getState(window).drag.webContents.getLastWebPreferences().sandbox, true);
  await page.loadFile(path.join(__dirname, "../../views/loading.html"));
  assert.deepEqual(failures, []);
  const observed = await page.executeJavaScript(`(async () => ({
    names: window.gideonDesktop.capabilities.names(),
    unsupported: await window.gideonDesktop.capabilities.probe("system_audio"),
    snapshot: await window.gideonDesktop.capabilities.snapshot(),
    requireType: typeof require,
    processType: typeof process
  }))()`);
  assert.deepEqual(observed.names, CAPABILITIES);
  assert.equal(observed.unsupported.available, false);
  assert.deepEqual(Object.keys(observed.snapshot).sort(), [...CAPABILITIES].sort());
  assert.equal(observed.requireType, "undefined");
  assert.equal(observed.processType, "undefined");
  await page.executeJavaScript(`window.bridgeStatus = new Promise(resolve => {
    const off = window.gideonDesktop.onStatus(payload => { off(); resolve(payload); });
  }); true`);
  page.send("status", "bridge-ready");
  assert.equal(await page.executeJavaScript("window.bridgeStatus"), "bridge-ready");
  const detached = workspace.mount(window, { attachBridge: false });
  assert.equal(detached.getLastWebPreferences().sandbox, true);
  await detached.loadFile(path.join(__dirname, "../../views/loading.html"));
  assert.equal(await detached.executeJavaScript("typeof window.gideonDesktop"), "undefined");
  console.log("Sandboxed dashboard preload: real Electron bridge, IPC, and isolation passed");
}

run().then(() => finish(0), (error) => { console.error(error); finish(1); });

function finish(code) {
  clearTimeout(timeout);
  if (window && !window.isDestroyed()) {
    workspace.releaseViews(window);
    window.destroy();
  }
  try { fs.rmSync(profile, { recursive: true, force: true }); }
  catch (error) { console.error(error); code = 1; }
  app.exit(code);
}
