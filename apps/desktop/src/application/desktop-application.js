"use strict";

const os = require("node:os");
const path = require("node:path");
const { LocalGateway } = require("./local-gateway");
const { EndpointSession, waitRemote } = require("./endpoint-session");
const { WindowWorkspace } = require("./window-workspace");
const { IPC_CHANNELS, makeCapabilities, registerCapabilityIpc } = require("../native/capabilities");
const { makePushToTalk, registerPushToTalkIpc } = require("../native/push-to-talk");
const { makeLoginItem, registerLoginItemIpc } = require("../native/login-item");
const { makeNativeNotifications, registerNativeNotificationIpc } = require("../native/notifications");
const { DEEP_LINKS, summarizePresence, makeTrayPresence, shouldHideOnClose, shouldQuitOnAllWindowsClosed } = require("../native/tray-presence");

function applicationMenu(actions) {
  const item = (label, action, accelerator) => ({ label, click: actions[action], ...(accelerator ? { accelerator } : {}) });
  return [{ role: "appMenu" }, { role: "editMenu" }, { label: "Tab", submenu: [
    item("New Tab", "newTab", "CmdOrCtrl+T"), item("Rename Tab…", "rename", "CmdOrCtrl+Shift+R"),
    { type: "separator" }, item("Merge All Windows", "merge"),
  ] }, { label: "Gateway", submenu: [item("Gateways…", "gateways", "CmdOrCtrl+Shift+G")] }, { role: "windowMenu" }];
}

class DesktopApplication {
  constructor(electron) {
    this.electron = electron;
    this.state = { window: null, quitting: false, shutdown: null, complete: false, presenceTimer: null };
    const home = process.env.GIDEON_HOME || path.join(os.homedir(), ".gideon");
    this.gateway = new LocalGateway({ app: electron.app, home, status: (message) => this.send("status", message) });
    this.workspace = new WindowWorkspace(electron, {
      main: () => this.state.window, target: () => this.endpoints.target(), local: () => this.gateway.url,
      waitLocal: (window) => this.gateway.waitReady(window), waitRemote,
      failedLoad: (window) => this.endpoints.scheduleReachability(window), openConnect: () => this.endpoints.openDialog(),
    });
    this.endpoints = new EndpointSession({ gateway: this.gateway, workspace: this.workspace, mainWindow: () => this.state.window,
      home, electron, quit: () => this.requestQuit() });
    this.capabilities = makeCapabilities({ platform: process.platform, systemPreferences: electron.systemPreferences,
      notification: electron.Notification, onChange: (capability, state) => {
        this.send(IPC_CHANNELS.state, { capability, state });
        this.gateway.publish(this.capabilities.snapshot());
      } });
    this.capture = makePushToTalk({ globalShortcut: electron.globalShortcut,
      send: (payload) => this.send(IPC_CHANNELS.pushToTalk, payload), onCapturing: (active) => this.tray.setCapturing(active) });
    this.login = makeLoginItem({ app: electron.app, log: (message) => console.warn(`login item: ${message}`) });
    this.notifications = makeNativeNotifications({ Notification: electron.Notification, focusWindow: () => this.show(),
      sendToRenderer: (payload) => this.send(IPC_CHANNELS.notificationActivate, payload), log: (message) => console.warn(message) });
    this.tray = makeTrayPresence({ TrayCtor: electron.Tray, MenuCtor: electron.Menu, nativeImageMod: electron.nativeImage,
      nativeTheme: electron.nativeTheme,
      iconPath: () => path.join(__dirname, `../../assets/tray-${electron.nativeTheme?.shouldUseDarkColors ? "dark" : "light"}.png`),
      log: (message) => console.warn(`tray: ${message}`),
      actions: { open: () => this.show(), deepLink: (hash) => this.deepLink(hash),
        quickCapture: () => this.deepLink(`${DEEP_LINKS.inbox}?capture=1`),
        toggleLoginItem: (enabled) => this.updateLogin(enabled), quit: () => this.requestQuit() } });
  }

  send(channel, payload) {
    try { this.state.window?.webContents?.send(channel, payload); } catch {}
  }

  show() {
    const window = this.state.window;
    if (!window || window.isDestroyed()) return false;
    if (!window.isVisible()) window.show();
    window.focus();
    return true;
  }

  deepLink(hash) {
    if (!this.show()) return;
    const page = this.state.window.webContents;
    if (!page || page.isDestroyed?.()) return;
    try { Promise.resolve(page.executeJavaScript(`window.location.hash = ${JSON.stringify(String(hash))}`))
      .catch((error) => console.warn(`deep link to ${hash} failed: ${error.message}`)); }
    catch (error) { console.warn(`deep link to ${hash} failed: ${error.message}`); }
  }

  syncLogin() { this.tray.setLoginItemState({ supported: this.login.supported, enabled: this.login.isEnabled() }); }

  updateLogin(enabled) {
    const result = this.login.set(enabled);
    if (!result.ok && result.reason) console.warn(`login item unchanged: ${result.reason}`);
    this.syncLogin();
  }

  configure() {
    const { app, ipcMain, Menu } = this.electron;
    const actions = { newTab: () => this.workspace.openTab(), rename: () => this.workspace.renameFocused(),
      merge: () => this.workspace.merge(), gateways: () => this.endpoints.openDialog() };
    Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenu(actions)));
    registerCapabilityIpc(ipcMain, this.capabilities);
    registerPushToTalkIpc(ipcMain, this.capture, IPC_CHANNELS);
    registerLoginItemIpc(ipcMain, this.login, IPC_CHANNELS, () => this.syncLogin());
    registerNativeNotificationIpc(ipcMain, this.notifications, IPC_CHANNELS);
    this.endpoints.initialize();
    if (!this.tray.start()) console.warn("running without menu-bar presence — the window will close on close");
    this.syncLogin();
    const window = this.workspace.create();
    this.state.window = window;
    this.workspace.mount(window);
    window.on("close", (event) => {
      if (shouldHideOnClose({ trayAvailable: this.tray.available, isQuitting: this.state.quitting })) {
        event.preventDefault();
        window.hide();
      }
    });
    app.on("activate", () => {
      if (this.state.window && !this.state.window.isDestroyed() && !this.state.window.isVisible()) this.state.window.show();
    });
    app.on("new-window-for-tab", actions.newTab);
  }

  async refreshPresence() {
    if (!this.tray.available) return;
    const requests = ["/api/approvals", "/api/loops"].map((route) => this.gateway.request("GET", route));
    const [approvals, loops] = await Promise.all(requests);
    if (!this.state.quitting) this.tray.setPresence(summarizePresence(approvals, loops));
  }

  startPresence() {
    if (!this.tray.available || this.state.presenceTimer !== null) return;
    this.refreshPresence();
    this.state.presenceTimer = setInterval(() => this.refreshPresence(), 5000);
  }

  async boot() {
    this.configure();
    try { await this.gateway.start(); }
    catch (error) { console.error("Gateway did not start:", error.message); }
    if (this.state.quitting) return;
    await this.gateway.register(this.capabilities.snapshot());
    if (this.state.quitting) return;
    this.startPresence();
    await this.endpoints.chooseStartup();
  }

  requestQuit() { this.state.quitting = true; this.electron.app.quit(); }

  beforeQuit(event) {
    this.state.quitting = true;
    if (this.state.complete) return;
    event.preventDefault();
    if (this.state.shutdown) return;
    this.capture.unbind();
    this.capture.clearCapturing();
    if (this.state.presenceTimer !== null) clearInterval(this.state.presenceTimer);
    this.state.presenceTimer = null;
    this.endpoints.cancelReachability();
    this.gateway.unregister();
    this.state.shutdown = this.gateway.stop()
      .catch((error) => console.warn(`gateway shutdown failed: ${error.message}`))
      .finally(() => { this.tray.destroy(); this.state.complete = true; this.electron.app.quit(); });
  }

  run() {
    const { app } = this.electron;
    app.name = "Gideon";
    app.on("before-quit", (event) => this.beforeQuit(event));
    app.on("window-all-closed", () => {
      if (shouldQuitOnAllWindowsClosed({ platform: process.platform, trayAvailable: this.tray.available })) app.quit();
    });
    if (!app.requestSingleInstanceLock()) { app.quit(); return; }
    app.on("second-instance", () => this.show());
    app.whenReady().then(() => this.boot()).catch((error) => console.error("Desktop startup failed:", error.message));
  }
}

module.exports = { DesktopApplication, applicationMenu };
