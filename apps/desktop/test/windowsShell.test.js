"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const Module = require("node:module");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { WindowWorkspace, hostedOAuthStart } = require("../src/application/window-workspace");
const { makeCapabilities } = require("../src/native/capabilities");
const { makeTrayPresence, buildTrayMenuTemplate, shouldHideOnClose } = require("../src/native/tray-presence");
const { openShellStore } = require("../src/storage/shell-store");

const originalLoad = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === "electron" && parent?.filename?.endsWith("/native/context-menu.js")) {
    return { Menu: { buildFromTemplate: (menu) => menu }, BrowserWindow: { fromWebContents: () => null } };
  }
  return originalLoad.call(this, request, parent, isMain);
};

function onPlatform(platform, action) {
  const original = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", { value: platform, configurable: true });
  try { return action(); }
  finally { Object.defineProperty(process, "platform", original); }
}

async function onPlatformAsync(platform, action) {
  const original = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", { value: platform, configurable: true });
  try { return await action(); }
  finally { Object.defineProperty(process, "platform", original); }
}

function desktopHarness() {
  const windows = [];
  const external = [];
  class Page extends EventEmitter {
    constructor() {
      super();
      this.session = { webRequest: { onBeforeSendHeaders: (handler) => { this.headers = handler; } } };
      this.styles = [];
    }
    setWindowOpenHandler(handler) { this.openHandler = handler; }
    loadURL(address) { this.url = address; return Promise.resolve(); }
    loadFile(file) { this.file = file; return Promise.resolve(); }
    insertCSS(style) { this.styles.push(style); }
    executeJavaScript() { return Promise.resolve(""); }
    close() { this.closed = true; }
    isDestroyed() { return false; }
  }
  class View {
    constructor(options) { this.options = options; this.webContents = new Page(); }
    setBackgroundColor(color) { this.background = color; }
    setBounds(bounds) { this.bounds = bounds; }
  }
  class BaseWindow extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.children = [];
      this.contentView = { addChildView: (view) => this.children.push(view),
        removeChildView: (view) => { this.children = this.children.filter((child) => child !== view); } };
      this.visible = true;
      windows.push(this);
    }
    getContentBounds() { return { width: 1200, height: 800 }; }
    isFullScreen() { return Boolean(this.fullscreen); }
    isDestroyed() { return Boolean(this.destroyed); }
    show() { this.visible = true; }
    hide() { this.visible = false; }
    focus() { this.focused = true; }
    isVisible() { return this.visible; }
    addTabbedWindow(tab) { this.tab = tab; }
    setTitle(title) { this.title = title; }
    setBackgroundColor(color) { this.background = color; }
  }
  class BrowserWindow extends EventEmitter {
    constructor(options) { super(); this.options = options; this.webContents = new Page(); BrowserWindow.latest = this; }
    loadURL(address) { this.url = address; }
    focus() { this.focused = true; }
    close() { this.closed = true; this.emit("closed"); }
  }
  const electron = { BaseWindow, BrowserWindow, WebContentsView: View, nativeTheme: {},
    shell: { openExternal: (address) => external.push(address) }, Menu: { buildFromTemplate: (items) => items } };
  return { electron, windows, external };
}

class TrayHandle extends EventEmitter {
  constructor(image) { super(); this.image = image; }
  setContextMenu(menu) { this.menu = menu; }
  setToolTip(tooltip) { this.tooltip = tooltip; }
  setTitle(title) { this.title = title; this.titleCalls = (this.titleCalls || 0) + 1; }
  destroy() { this.destroyed = true; }
}

function makeTray(mode, platform) {
  let handle;
  const actions = [];
  const tray = makeTrayPresence({ mode, platform, TrayCtor: class extends TrayHandle {
    constructor(image) { super(image); handle = this; }
  }, MenuCtor: { buildFromTemplate: (menu) => menu }, nativeImageMod: { createFromPath: () => ({ isEmpty: () => false }) },
  iconPath: "/gideon.png", actions: { open: () => actions.push("open"), quit: () => actions.push("quit"),
    toggleLoginItem: (enabled) => actions.push(enabled) } });
  return { tray, get handle() { return handle; }, actions };
}

test("Windows uses native title buttons and full-height content without privileged remote preload", () => {
  onPlatform("win32", () => {
    const { electron, windows, external } = desktopHarness();
    const workspace = new WindowWorkspace(electron, { target: () => "https://cloud.example", local: () => "" });
    const window = workspace.create();
    const page = workspace.mount(window, { attachBridge: false });
    assert.equal(window.options.titleBarStyle, undefined);
    assert.equal(window.options.tabbingIdentifier, undefined);
    assert.equal(window.children.length, 1);
    assert.equal(window.children[0].options.webPreferences.preload, undefined);
    assert.equal(window.children[0].options.webPreferences.nodeIntegration, false);
    assert.equal(window.children[0].options.webPreferences.sandbox, true);
    assert.deepEqual(window.children[0].bounds, { x: 0, y: 0, width: 1200, height: 800 });
    assert.equal(window.listenerCount("system-context-menu"), 0);
    page.emit("did-finish-load");
    assert.equal(page.styles.length, 0);
    let blocked = false;
    page.emit("will-navigate", { preventDefault: () => { blocked = true; } }, "https://foreign.example/path");
    assert.equal(blocked, true);
    assert.deepEqual(external, ["https://foreign.example/path"]);
    assert.deepEqual(page.openHandler({ url: "https://cloud.example/report" }), { action: "allow" });
    window.emit("closed");
    assert.equal(page.closed, true);
    assert.equal(window.children.length, 0);
    assert.equal(windows.length, 1);
  });
});

test("Mac and Linux keep the custom title strip and local preload", () => {
  for (const platform of ["darwin", "linux"]) onPlatform(platform, () => {
    const { electron } = desktopHarness();
    const workspace = new WindowWorkspace(electron, { target: () => "http://localhost:8080", local: () => "http://localhost:8080" });
    const window = workspace.create();
    const page = workspace.mount(window);
    assert.equal(window.options.titleBarStyle, "hidden");
    assert.equal(window.children.length, 2);
    assert.match(window.children[0].options.webPreferences.preload, /dashboard-preload\.js$/);
    assert.equal(window.children[0].bounds.y, 28);
    window.fullscreen = true;
    window.emit("enter-full-screen");
    assert.equal(window.children[0].bounds.y, 0);
    window.children[1].webContents.emit("did-finish-load");
    assert.match(window.children[1].webContents.styles[0], /-webkit-app-region: drag/);
    page.emit("did-finish-load");
    assert.match(page.styles[0], /gideon-window-drag/);
  });
});

test("Hosted Windows opens a separate window without a bridge, even for loopback endpoints", async () => {
  await onPlatformAsync("win32", async () => {
    const harness = desktopHarness();
    const workspace = new WindowWorkspace(harness.electron, { main: () => main, target: () => "http://localhost:8443",
      local: () => "", hostedMode: () => true, waitRemote: async () => {} });
    const main = workspace.create();
    workspace.mount(main, { attachBridge: false });
    await workspace.openTab();
    const tab = harness.windows[1];
    assert.ok(tab);
    assert.equal(main.tab, undefined);
    assert.equal(tab.visible, true);
    assert.equal(tab.focused, true);
    assert.equal(tab.children.length, 1);
    assert.equal(tab.children[0].options.webPreferences.preload, undefined);
    assert.equal(tab.webContents.url, "http://localhost:8443");
    assert.equal(workspace.hasBridge(tab), false);
  });
});

test("Mac tabbing remains native while Windows merge is unavailable", async () => {
  await onPlatformAsync("darwin", async () => {
    const harness = desktopHarness();
    const workspace = new WindowWorkspace(harness.electron, { main: () => main, target: () => "http://localhost:8443",
      local: () => "http://localhost:8443", waitLocal: async () => {} });
    const main = workspace.create();
    workspace.mount(main);
    await workspace.openTab();
    assert.equal(main.tab, harness.windows[1]);
    assert.match(main.tab.children[0].options.webPreferences.preload, /dashboard-preload\.js$/);
  });
  onPlatform("win32", () => {
    const harness = desktopHarness();
    const workspace = new WindowWorkspace(harness.electron, { main: () => main });
    const main = workspace.create();
    main.visible = false;
    workspace.merge();
    assert.equal(main.tab, undefined);
    assert.equal(main.visible, false);
  });
});

test("Windows menu describes windows and does not offer macOS merge", () => {
  const { applicationMenu } = require("../src/application/desktop-application");
  const actions = { newTab() {}, rename() {}, merge() {}, gateways() {} };
  const windows = applicationMenu(actions, "win32");
  assert.equal(windows[2].label, "Window");
  assert.deepEqual(windows[2].submenu.map((entry) => entry.label), ["New Window", "Rename Window…"]);
  assert.equal(windows[2].submenu[0].accelerator, "CmdOrCtrl+N");
  const mac = applicationMenu(actions, "darwin");
  assert.equal(mac[2].label, "Tab");
  assert.ok(mac[2].submenu.some((entry) => entry.label === "Merge All Windows"));
});

test("Hosted OAuth opens a sandboxed window and returns to the original hosted view", () => {
  const hosted = "https://cloud.example";
  const authorize = "https://auth.example/auth/v1/authorize?provider=google&redirect_to=" +
    encodeURIComponent(hosted + "/?return_to=%23%2Fprojects");
  const badProvider = authorize.replace("provider=google", "provider=attacker");
  const badReturn = authorize.replace(encodeURIComponent(hosted), encodeURIComponent("https://attacker.example"));
  assert.equal(hostedOAuthStart(authorize, hosted), true);
  assert.equal(hostedOAuthStart(badProvider, hosted), false);
  assert.equal(hostedOAuthStart(badReturn, hosted), false);
  assert.equal(hostedOAuthStart("javascript:alert(1)", hosted), false);
  const { electron, external } = desktopHarness();
  const workspace = new WindowWorkspace(electron, { target: () => hosted, local: () => "", hostedMode: () => true });
  const window = workspace.create();
  const page = workspace.mount(window, { attachBridge: false });
  let prevented = false;
  page.emit("will-navigate", { preventDefault: () => { prevented = true; } }, authorize);
  const auth = electron.BrowserWindow.latest;
  assert.equal(prevented, true);
  assert.equal(auth.url, authorize);
  assert.deepEqual(auth.options.webPreferences, { nodeIntegration: false, contextIsolation: true,
    sandbox: true, webSecurity: true, webviewTag: false });
  assert.equal(auth.options.webPreferences.preload, undefined);
  assert.equal(auth.webContents.openHandler({ url: "https://unrelated.example" }).action, "deny");
  let denied = false;
  auth.webContents.emit("will-navigate", { preventDefault: () => { denied = true; } }, "javascript:alert(1)");
  assert.equal(denied, true);
  denied = false;
  auth.webContents.emit("will-redirect", { preventDefault: () => { denied = true; } }, "file:///tmp/unsafe.html");
  assert.equal(denied, true);
  let providerBlocked = false;
  auth.webContents.emit("will-redirect", { preventDefault: () => { providerBlocked = true; } }, "https://accounts.google.com/signin");
  assert.equal(providerBlocked, false);
  assert.equal(auth.closed, undefined);
  assert.equal(page.url, undefined);
  let lookalikeBlocked = false;
  auth.webContents.emit("will-redirect", { preventDefault: () => { lookalikeBlocked = true; } },
    "https://cloud.example.attacker.test/callback");
  assert.equal(lookalikeBlocked, false);
  assert.equal(page.url, undefined);
  assert.equal(auth.options.webPreferences.nodeIntegration, false);
  assert.equal(auth.options.webPreferences.preload, undefined);
  const returned = hosted + "/?code=oauth-code&return_to=%23%2Fprojects";
  let returnIntercepted = false;
  auth.webContents.emit("will-redirect", { preventDefault: () => { returnIntercepted = true; } }, returned);
  assert.equal(returnIntercepted, true);
  assert.equal(page.url, returned);
  assert.equal(auth.closed, true);
  assert.deepEqual(external, []);
});

test("Windows tray opens hidden app and omits local gateway status", () => {
  const setup = makeTray("hosted", "win32");
  const { tray, actions } = setup;
  assert.equal(tray.start(), true);
  const handle = setup.handle;
  const item = (label) => handle.menu.find((row) => row.label === label);
  assert.equal(handle.titleCalls, undefined);
  assert.equal(handle.tooltip, "Gideon — hosted workspace");
  assert.ok(item("Open Gideon"));
  assert.equal(item("No approvals waiting"), undefined);
  assert.equal(item("Gateways…"), undefined);
  tray.setLoginItemState({ supported: true, enabled: true });
  assert.equal(item("Open at Login").checked, true);
  item("Open Gideon").click();
  handle.emit("click");
  item("Quit Gideon").click();
  assert.deepEqual(actions, ["open", "open", "quit"]);
  assert.equal(shouldHideOnClose({ trayAvailable: tray.available, isQuitting: false }), true);
  tray.destroy();
  assert.equal(tray.available, false);
  assert.equal(shouldHideOnClose({ trayAvailable: tray.available, isQuitting: false }), false);
  assert.equal(handle.destroyed, true);
});

test("Local tray menu retains approvals and loop links", () => {
  const menu = buildTrayMenuTemplate({ mode: "local", presence: { connected: true, approvals: 2,
    running: [{ id: "run-1", label: "Active" }] } });
  assert.ok(menu.some((item) => item.label === "2 approvals waiting"));
  assert.ok(menu.some((item) => item.label === "1 loop running"));
});

test("Windows capabilities reflect live shell availability without POSIX or macOS claims", async () => {
  let tray = false;
  const caps = makeCapabilities({ platform: "win32", notification: { isSupported: () => true },
    shellAvailability: { tray: () => tray, global_hotkey: false, login_item: true } });
  assert.equal(caps.probe("tray").available, false);
  tray = true;
  assert.equal(caps.probe("tray").granted, "granted");
  assert.equal(caps.probe("global_hotkey").granted, "unavailable");
  assert.equal(caps.probe("login_item").available, true);
  assert.equal(caps.probe("native_notifications").granted, "not-determined");
  assert.match(caps.probe("native_notifications").reason, /Windows/);
  assert.equal(caps.probe("audio_capture").available, false);
  assert.equal(caps.probe("screen_capture").available, false);
  assert.equal((await caps.request("tray")).state, "granted");
  assert.equal((await caps.request("global_hotkey")).state, "unavailable");
  assert.equal(makeCapabilities({ platform: "win32" }).probe("native_notifications").available, false);
  assert.equal(makeCapabilities({ platform: "win32", notification: { isSupported: () => false } })
    .probe("native_notifications").available, false);
  assert.equal(makeCapabilities({ platform: "darwin" }).probe("tray").available, true);
  assert.equal(makeCapabilities({ platform: "linux" }).probe("tray").available, false);
});

test("Windows endpoint store survives restart and reports ACL status honestly", (context) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "gideon-win-store-"));
  context.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const first = openShellStore({ home, platform: "win32" });
  assert.equal(first.permissions.safe, false);
  assert.equal(first.permissions.ownerOnly, null);
  first.setItem("endpoint", "https://cloud.example");
  first.setItem("selection", "ep_cloud");
  assert.equal(first.readOnly, false);
  const second = openShellStore({ home, platform: "win32" });
  assert.equal(second.getItem("endpoint"), "https://cloud.example");
  assert.equal(second.getItem("selection"), "ep_cloud");
  assert.equal(second.permissions.reason, "windows_acl_not_verified");
  assert.equal(second.permissions.mode, null);
  second.removeItem("selection");
  assert.equal(openShellStore({ home, platform: "win32" }).getItem("selection"), null);
  assert.equal(openShellStore({ home, platform: "win32" }).getItem("endpoint"), "https://cloud.example");
});

test("Packaged Windows boot, close, reopen, and quit never invoke the POSIX gateway", async (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "gideon-win-app-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const resources = path.join(root, "resources");
  fs.mkdirSync(resources);
  fs.writeFileSync(path.join(resources, "hosted-config.json"), JSON.stringify({ schemaVersion: 1,
    mode: "hosted", hostedUrl: "https://cloud.example" }));
  const originalResources = Object.getOwnPropertyDescriptor(process, "resourcesPath");
  const originalHome = process.env.GIDEON_HOME;
  Object.defineProperty(process, "resourcesPath", { value: resources, configurable: true });
  process.env.GIDEON_HOME = path.join(root, "home");
  try {
    await onPlatformAsync("win32", async () => {
      const { DesktopApplication } = require("../src/application/desktop-application");
      const harness = desktopHarness();
      const app = new EventEmitter();
      app.isPackaged = true;
      app.getLoginItemSettings = () => ({ openAtLogin: false });
      app.quit = () => { app.quitCalls = (app.quitCalls || 0) + 1; };
      let trayHandle;
      const electron = { ...harness.electron, app, ipcMain: { handle() {} },
        Menu: { buildFromTemplate: (menu) => menu, setApplicationMenu() {} },
        Tray: class extends TrayHandle { constructor(image) { super(image); trayHandle = this; } },
        nativeImage: { createFromPath: () => ({ isEmpty: () => false }) },
        nativeTheme: new EventEmitter(), globalShortcut: {},
        Notification: class { static isSupported() { return true; } } };
      const desktop = new DesktopApplication(electron);
      assert.equal(desktop.hostedMode, true);
      const gatewayCalls = [];
      for (const method of ["start", "register", "publish", "unregister", "stop"]) {
        desktop.gateway[method] = () => { gatewayCalls.push(method); throw new Error(`gateway ${method} was used`); };
      }
      let startup = 0;
      desktop.endpoints.chooseStartup = async () => { startup++; };
      await desktop.boot();
      assert.equal(startup, 1);
      assert.deepEqual(gatewayCalls, []);
      const window = desktop.state.window;
      assert.equal(window.children[0].options.webPreferences.preload, undefined);
      let prevented = false;
      window.emit("close", { preventDefault: () => { prevented = true; } });
      assert.equal(prevented, true);
      assert.equal(window.isVisible(), false);
      trayHandle.emit("click");
      assert.equal(window.isVisible(), true);
      assert.equal(window.focused, true);
      let quitPrevented = false;
      desktop.beforeQuit({ preventDefault: () => { quitPrevented = true; } });
      await desktop.state.shutdown;
      assert.equal(quitPrevented, true);
      assert.equal(desktop.state.complete, true);
      assert.equal(trayHandle.destroyed, true);
      assert.equal(app.quitCalls, 1);
      assert.deepEqual(gatewayCalls, []);
    });
  } finally {
    if (originalResources) Object.defineProperty(process, "resourcesPath", originalResources);
    else delete process.resourcesPath;
    if (originalHome === undefined) delete process.env.GIDEON_HOME;
    else process.env.GIDEON_HOME = originalHome;
  }
});
