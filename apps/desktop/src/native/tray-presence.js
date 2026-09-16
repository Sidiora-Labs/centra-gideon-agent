"use strict";

const DEEP_LINKS = { dashboard: "#/dashboard", approvals: "#/chat", inbox: "#/inbox", settings: "#/settings",
  loop: (id) => "#/loops/" + encodeURIComponent(String(id)) };
const RUNNING_STATUS = "running", MAX_LISTED_LOOPS = 8;
const EMPTY_PRESENCE = Object.freeze({ approvals: 0, running: [], connected: false });
const separator = () => ({ type: "separator" });

function truncateLabel(text, max = 48) {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length <= max ? normalized : normalized.slice(0, max - 1) + "…";
}

function summarizePresence(approvalsPayload, loopsPayload) {
  const pending = Array.isArray(approvalsPayload) ? approvalsPayload : approvalsPayload?.approvals ?? approvalsPayload?.items;
  const approvals = Array.isArray(pending) ? pending.length : Number.isFinite(approvalsPayload?.pending) ? Number(approvalsPayload.pending) : 0;
  const rows = Array.isArray(loopsPayload) ? loopsPayload : Array.isArray(loopsPayload?.loops) ? loopsPayload.loops : [];
  const running = [];
  for (const row of rows) {
    if (!row || typeof row !== "object" || String(row.status || "").toLowerCase() !== RUNNING_STATUS || String(row.id ?? "") === "") continue;
    running.push({ id: String(row.id), label: truncateLabel(String(row.name || row.task || row.goal || row.id || "Untitled loop")) });
  }
  return { approvals, running, connected: approvalsPayload != null };
}

function composeTrayTitle({ capturing = false, approvals = 0 } = {}) {
  return capturing ? "● Listening" : Number(approvals) > 0 ? String(Number(approvals)) : "";
}

function composeTrayTooltip({ capturing = false, approvals = 0, running = [], connected = false } = {}) {
  if (capturing) return "Gideon is listening — press the shortcut again to stop";
  if (!connected) return "Gideon — not connected to the gateway";
  return `Gideon — ${approvals} approval${approvals === 1 ? "" : "s"} waiting, ${running.length} loop${running.length === 1 ? "" : "s"} running`;
}

function buildTrayMenuTemplate({ presence = EMPTY_PRESENCE, loginItem = { supported: false, enabled: false }, actions = {}, tiles = [] } = {}) {
  const { approvals, running, connected } = { ...EMPTY_PRESENCE, ...presence };
  const call = (name, ...arguments_) => { if (actions[name]) return actions[name](...arguments_); };
  const status = [{ label: "Gideon — " + (connected ? "connected" : "not connected"), enabled: false }];
  const work = [{ label: approvals === 0 ? "No approvals waiting" : `${approvals} approval${approvals === 1 ? "" : "s"} waiting`,
    click: () => call("deepLink", DEEP_LINKS.approvals) }];
  work.push(running.length ? { label: `${running.length} loop${running.length === 1 ? "" : "s"} running`,
    submenu: running.slice(0, MAX_LISTED_LOOPS).map((loop) => ({ label: loop.label, click: () => call("deepLink", DEEP_LINKS.loop(loop.id)) })) }
    : { label: "No loops running", enabled: false });
  const navigation = [{ label: "Quick Capture Note…", click: () => call("quickCapture") },
    { label: "Open Dashboard", click: () => call("open") }];
  const ambient = (Array.isArray(tiles) ? tiles : []).filter((tile) => tile && typeof tile.label === "string")
    .map((tile) => ({ label: tile.label, click: tile.click || (() => {}) }));
  const settings = [{ label: "Open at Login", type: "checkbox", checked: Boolean(loginItem.enabled), enabled: Boolean(loginItem.supported),
    click: (item) => call("toggleLoginItem", item ? Boolean(item.checked) : !loginItem.enabled) }];
  return [status, work, navigation, ambient, settings, [{ label: "Quit Gideon", click: () => call("quit") }]]
    .filter((section) => section.length).flatMap((section, index) => index ? [separator(), ...section] : section);
}

const shouldHideOnClose = ({ trayAvailable, isQuitting }) => Boolean(trayAvailable) && !isQuitting;
const shouldQuitOnAllWindowsClosed = ({ platform, trayAvailable }) => platform !== "darwin" || !trayAvailable;

class TrayController {
  constructor(dependencies) {
    this.dependencies = dependencies;
    this.log = dependencies.log || (() => {});
    this.actions = dependencies.actions || {};
    this.handle = null;
    this.onAppearance = () => {
      try {
        const image = this.icon();
        if (image) this.handle?.setImage?.(image);
      } catch (error) { this.log(`tray appearance update skipped: ${error.message}`); }
    };
    this.state = { presence: { ...EMPTY_PRESENCE }, loginItem: { supported: false, enabled: false }, capturing: false, tiles: [] };
  }

  icon() {
    const { nativeImageMod } = this.dependencies;
    const iconPath = typeof this.dependencies.iconPath === "function" ? this.dependencies.iconPath() : this.dependencies.iconPath;
    if (!nativeImageMod || !iconPath) return null;
    try {
      const image = nativeImageMod.createFromPath(iconPath);
      if (!image || image.isEmpty?.()) { this.log(`tray icon at ${iconPath} is empty or unreadable`); return null; }
      return typeof image.resize === "function" ? image.resize({ width: 18, height: 18 }) : image;
    } catch (error) { this.log(`tray icon failed to load: ${error.message}`); return null; }
  }

  start() {
    if (this.handle) return true;
    const { TrayCtor, MenuCtor } = this.dependencies;
    if (!TrayCtor || !MenuCtor) { this.log("no Tray implementation on this platform — running without menu-bar presence"); return false; }
    const icon = this.icon();
    if (!icon) { this.log("skipping menu-bar presence — no usable icon"); return false; }
    try { this.handle = new TrayCtor(icon); }
    catch (error) { this.log(`menu-bar presence unavailable: ${error.message}`); return false; }
    try { this.handle.on("click", () => this.actions.open?.()); }
    catch (error) { this.log(`tray click handler could not be attached: ${error.message}`); }
    this.dependencies.nativeTheme?.on?.("updated", this.onAppearance);
    this.render();
    return true;
  }

  update(key, value) { this.state[key] = value; this.render(); }

  render() {
    if (!this.handle) return;
    const { presence, capturing } = this.state;
    try {
      const menu = this.dependencies.MenuCtor.buildFromTemplate(buildTrayMenuTemplate({ ...this.state, actions: this.actions }));
      this.handle.setContextMenu(menu);
      this.handle.setTitle(composeTrayTitle({ capturing, approvals: presence.approvals }));
      this.handle.setToolTip(composeTrayTooltip({ capturing, ...presence }));
    } catch (error) { this.log(`tray render skipped: ${error.message}`); }
  }

  destroy() {
    this.dependencies.nativeTheme?.removeListener?.("updated", this.onAppearance);
    const handle = this.handle;
    this.handle = null;
    try { handle?.destroy(); }
    catch (error) { this.log(`tray destroy skipped: ${error.message}`); }
  }
}

function makeTrayPresence(dependencies = {}) {
  const controller = new TrayController(dependencies);
  return { get available() { return controller.handle !== null; }, start: () => controller.start(), render: () => controller.render(),
    destroy: () => controller.destroy(), setPresence: (next) => controller.update("presence", { ...EMPTY_PRESENCE, ...(next || {}) }),
    setLoginItemState: (next) => controller.update("loginItem", { supported: false, enabled: false, ...(next || {}) }),
    setCapturing: (value) => controller.update("capturing", Boolean(value)),
    setTiles: (value) => controller.update("tiles", Array.isArray(value) ? value : []) };
}

module.exports = { DEEP_LINKS, RUNNING_STATUS, MAX_LISTED_LOOPS, EMPTY_PRESENCE, summarizePresence, composeTrayTitle,
  composeTrayTooltip, buildTrayMenuTemplate, shouldHideOnClose, shouldQuitOnAllWindowsClosed, makeTrayPresence, truncateLabel };
