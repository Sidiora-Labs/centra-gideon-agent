"use strict";

const path = require("node:path");
const { attachContextMenu } = require("../native/context-menu");
const { shouldAttachBridge } = require("../connection/controller");
const TAB_BAR_HEIGHT = 28;
const escapeAttribute = (text) => String(text).replace(/[&"<>]/g, (character) =>
  ({ "&": "&amp;", '"': "&quot;", "<": "&lt;", ">": "&gt;" })[character]);

function modalStyles(colors, dark) {
  const defaults = dark
    ? { bg: "#112235", card: "#081523", text: "#eaf2ff", muted: "#94adc8", border: "#263c52", bgAccent: "#21374c" }
    : { bg: "#f5f9ff", card: "#ffffff", text: "#152b43", muted: "#57728e", border: "#c8d7e8", bgAccent: "#e5edf7" };
  const palette = { ...defaults, accent: "#68a8ff", accentHover: "#4b91ec", ...(colors?.bg ? colors : {}) };
  return `*{box-sizing:border-box}body{margin:0;padding:24px;font:14px system-ui,sans-serif;background:${palette.bg};color:${palette.text}}
    label{display:block;margin-bottom:10px;color:${palette.muted}}input{width:100%;padding:11px;border:1px solid ${palette.border};border-radius:9px;background:${palette.card};color:${palette.text};font:inherit;outline:none}
    input:focus{border-color:${palette.accent}}.row{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}button{padding:9px 18px;border:0;border-radius:8px;font:inherit;cursor:pointer}
    .ok{background:${palette.accent};color:#07111f}.ok:hover{background:${palette.accentHover || palette.accent}}.cancel{background:${palette.bgAccent || palette.card};color:${palette.muted}}`;
}

function navigationDecision(candidate, origin, allowLocalDocument = false) {
  let address;
  try { address = new URL(candidate); }
  catch { return { allowed: false, external: "", origin: "" }; }
  const sameOrigin = Boolean(origin) && address.origin === origin;
  const localDocument = allowLocalDocument && ["file:", "about:"].includes(address.protocol);
  const allowed = sameOrigin || localDocument;
  return { allowed, external: !allowed && ["http:", "https:"].includes(address.protocol) ? address.href : "", origin: address.origin };
}

class WindowWorkspace {
  constructor(electron, actions) {
    this.electron = electron;
    this.actions = actions;
    this.windows = new WeakMap();
  }

  getState(window) { return this.windows.get(window); }
  hasBridge(window) { return Boolean(this.getState(window)?.bridge); }

  create() {
    return new this.electron.BaseWindow({ width: 1280, height: 860, minWidth: 550, minHeight: 600,
      tabbingIdentifier: "gideon", titleBarStyle: "hidden", backgroundColor: "#0a1320" });
  }

  layout(window) {
    const current = this.getState(window);
    if (!current?.content || window.isDestroyed()) return;
    const { width, height } = window.getContentBounds();
    const offset = window.isFullScreen() ? 0 : TAB_BAR_HEIGHT;
    current.drag.setBounds({ x: 0, y: 0, width, height: offset });
    current.content.setBounds({ x: 0, y: offset, width, height: height - offset });
  }

  releaseViews(window) {
    const current = this.getState(window);
    if (!current) return;
    for (const view of [current.content, current.drag]) {
      if (!view) continue;
      if (!window.isDestroyed()) {
        try { window.contentView.removeChildView(view); }
        catch (error) { console.warn(`could not detach a desktop view: ${error.message}`); }
      }
      view.webContents.close();
    }
    current.content = current.drag = null;
  }

  title(window) {
    if (window.isDestroyed()) return;
    const name = this.getState(window)?.name;
    window.setTitle(name ? `Gideon ${name}` : "Gideon");
  }

  synchronizeTheme(window) {
    const view = this.getState(window)?.content;
    if (!view || window.isDestroyed()) return;
    view.webContents.executeJavaScript('document.documentElement.dataset.mode || ""')
      .then((mode) => { if (["dark", "light"].includes(mode)) this.electron.nativeTheme.themeSource = mode; })
      .catch(() => {});
  }

  wireWindow(window) {
    for (const event of ["resize", "enter-full-screen", "leave-full-screen"]) window.on(event, () => this.layout(window));
    window.on("focus", () => this.synchronizeTheme(window));
    window.on("closed", () => { this.releaseViews(window); this.windows.delete(window); });
    window.on("system-context-menu", (event, point) => {
      event.preventDefault();
      this.electron.Menu.buildFromTemplate([
        { label: "Rename Tab…", click: () => this.renameFocused() }, { type: "separator" },
        { label: "New Tab", click: () => this.openTab() }, { label: "Merge All Windows", click: () => this.merge() },
        { type: "separator" }, { label: "Gateways…", click: () => this.actions.openConnect() },
      ]).popup({ window, x: point.x, y: point.y });
    });
  }

  mount(window, { attachBridge = true } = {}) {
    let current = this.getState(window);
    if (!current) {
      current = { name: null, bridge: false, content: null, drag: null };
      this.windows.set(window, current);
      this.wireWindow(window);
    } else {
      this.releaseViews(window);
    }
    const { WebContentsView } = this.electron;
    const content = new WebContentsView({ webPreferences: {
      ...(attachBridge ? { preload: path.join(__dirname, "../bridge/dashboard-preload.js") } : {}),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    } });
    const drag = new WebContentsView({ webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } });
    current.bridge = attachBridge;
    current.content = content;
    current.drag = drag;
    for (const view of [content, drag]) {
      view.setBackgroundColor("#00000000");
      window.contentView.addChildView(view);
    }
    drag.webContents.on("did-finish-load", () => {
      drag.webContents.insertCSS("html { -webkit-app-region: drag; height: 100%; }");
    });
    drag.webContents.loadURL("about:blank");
    window.webContents = content.webContents;
    this.layout(window);
    this.wirePage(window, content.webContents);
    return content.webContents;
  }

  origin() {
    const target = this.actions.target();
    try { return target ? new URL(target).origin : ""; }
    catch { return ""; }
  }

  wirePage(window, page) {
    attachContextMenu(page);
    const allowedOrigin = () => this.origin();
    page.setWindowOpenHandler(({ url }) => {
      const decision = navigationDecision(url, allowedOrigin());
      if (decision.external) this.electron.shell.openExternal(decision.external);
      return { action: decision.allowed ? "allow" : "deny" };
    });
    const guardNavigation = (event, url) => {
      const origin = allowedOrigin();
      const decision = navigationDecision(url, origin, true);
      if (decision.allowed) return;
      event.preventDefault();
      console.warn(`blocked in-app navigation to ${decision.origin} (allowed: ${origin || "none"})`);
      if (decision.external) this.electron.shell.openExternal(decision.external);
    };
    page.on("will-navigate", guardNavigation);
    page.on("will-redirect", guardNavigation);
    page.on("page-title-updated", (event) => { event.preventDefault(); this.title(window); });
    page.on("did-finish-load", () => {
      this.title(window);
      this.decoratePage(window, page);
      this.synchronizeTheme(window);
    });
    page.on("did-fail-load", (_event, code, description, url, isMainFrame) => {
      if (!isMainFrame || code === -3 || !this.actions.target() || this.actions.target() === this.actions.local()) return;
      console.warn(`load failed for ${url}: ${description} (${code})`);
      this.actions.failedLoad(window);
    });
    page.session.webRequest.onBeforeSendHeaders((details, callback) => {
      delete details.requestHeaders.Referer;
      callback({ requestHeaders: details.requestHeaders });
    });
  }

  decoratePage(window, page) {
    page.insertCSS(`#gideon-window-drag{position:fixed;top:0;left:0;right:0;height:52px;-webkit-app-region:drag;z-index:99999;pointer-events:none}
      a,button,input,select,textarea,[role="button"],[tabindex]{-webkit-app-region:no-drag}`);
    page.executeJavaScript(`(() => {
      if (!document.getElementById('gideon-window-drag')) {
        const region = document.createElement('div'); region.id = 'gideon-window-drag'; document.body.prepend(region);
      }
      return getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
    })()`)
      .then((background) => { if (background && !window.isDestroyed()) window.setBackgroundColor(background); })
      .catch(() => {});
  }

  async openTab() {
    const main = this.actions.main(), target = this.actions.target();
    if (!main || main.isDestroyed() || !target) return;
    main.show();
    const tab = this.create();
    const page = this.mount(tab, { attachBridge: shouldAttachBridge(target) });
    main.addTabbedWindow(tab);
    page.loadFile(path.join(__dirname, "../../views/loading.html"));
    try {
      await (target === this.actions.local() ? this.actions.waitLocal(tab) : this.actions.waitRemote(tab, target));
      if (!tab.isDestroyed()) page.loadURL(target);
    } catch { if (!tab.isDestroyed()) tab.destroy(); }
  }

  async colors() {
    const window = this.electron.BaseWindow.getFocusedWindow() || this.actions.main();
    if (!window || window.isDestroyed()) return null;
    try {
      return await window.webContents.executeJavaScript(`(() => {
        const styles = getComputedStyle(document.documentElement);
        return Object.fromEntries(['bg','card','text','muted','border','accent','accent-hover','bg-accent'].map(name =>
          [name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), styles.getPropertyValue('--' + name).trim()]));
      })()`);
    } catch { return null; }
  }

  async renameFocused() {
    const focused = this.electron.BaseWindow.getFocusedWindow();
    if (!focused || !this.windows.has(focused)) return;
    const currentName = focused.getTitle().replace(/^Gideon /g, "");
    const colors = await this.colors();
    if (focused.isDestroyed()) return;
    const prompt = new this.electron.BrowserWindow({ width: 420, height: 190, resizable: false, useContentSize: true,
      parent: focused, modal: true, backgroundColor: "#00000000", webPreferences: { nodeIntegration: false, contextIsolation: true } });
    const document = `<!doctype html><html><head><style>${modalStyles(colors, this.electron.nativeTheme.shouldUseDarkColors)}</style></head>
      <body><form><label for="name">Tab name</label><input id="name" value="${escapeAttribute(currentName)}" autofocus>
      <div class="row"><button type="button" class="cancel" onclick="window.close()">Cancel</button><button class="ok">Rename</button></div></form>
      <script>document.querySelector('form').addEventListener('submit', event => {event.preventDefault(); document.title=document.getElementById('name').value.trim(); window.close()});
      document.addEventListener('keydown', event => {if(event.key==='Escape')window.close()});</script></body></html>`;
    let submitted = null;
    prompt.on("page-title-updated", (_event, value) => { submitted = value; });
    prompt.on("closed", () => {
      const current = this.getState(focused);
      if (submitted && current && !focused.isDestroyed()) { current.name = submitted; this.title(focused); }
    });
    prompt.setMenu(null);
    prompt.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(document));
  }

  merge() {
    const main = this.actions.main();
    if (!main || main.isDestroyed()) return;
    main.show();
    for (const candidate of this.electron.BaseWindow.getAllWindows()) {
      if (candidate !== main && !candidate.isDestroyed() && this.windows.has(candidate)) main.addTabbedWindow(candidate);
    }
    setTimeout(() => {
      if (main.isDestroyed()) return;
      main.setHasShadow(false);
      main.setHasShadow(true);
    }, 50);
  }
}

module.exports = { WindowWorkspace, modalStyles, navigationDecision };
