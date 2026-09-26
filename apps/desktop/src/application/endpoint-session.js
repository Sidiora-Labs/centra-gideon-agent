"use strict";

const path = require("node:path");
const { openShellStore } = require("../storage/shell-store");
const { loadRegistry, saveRegistry, setActive } = require("../storage/endpoint-registry");
const { withHostedEndpoint, hostedGatewayUrl, configureHostedEndpoint, HOSTED_ENDPOINT_ID } = require("../connection/hosted");
const { readHostedConfig } = require("../connection/hosted-config");
const policy = require("../connection/controller");
const { describeList, makeConnectDialog } = require("../connection/dialog");
const { START_TIMEOUT, pause } = require("./local-gateway");

const isLocalRow = (row) => row?.id === policy.LOCAL_ENDPOINT_ID || row?.kind === "local";
const alive = (window) => Boolean(window) && !window.isDestroyed();

async function waitRemote(window, address) {
  const deadline = Date.now() + START_TIMEOUT;
  while (true) {
    if (!alive(window)) throw new Error("Window closed");
    if (Date.now() > deadline) throw new Error("Endpoint timeout");
    const probe = await policy.probeEndpoint(address, { timeoutMs: 2500 });
    if (probe.status === policy.HEALTH_REACHABLE) return probe;
    if (policy.nextReconnectStep({ status: probe.status, attempt: 0 }).action !== "retry") {
      throw Object.assign(new Error(`endpoint ${probe.status}`), { probe });
    }
    await pause(500);
  }
}

class EndpointSession {
  constructor({ gateway, workspace, mainWindow, home, electron, quit, hostedConfigOptions = {} }) {
    Object.assign(this, { gateway, workspace, mainWindow, home, electron, quit });
    this.hostedConfig = readHostedConfig({ isPackaged: electron.app.isPackaged, ...hostedConfigOptions });
    configureHostedEndpoint(this.hostedConfig);
    this.state = { activeUrl: null, store: null, health: {}, warnings: [], timer: null, attempt: 0, epoch: 0 };
    this.dialog = null;
  }

  initialize() {
    this.state.store = openShellStore({ home: this.home, log: (message) => console.warn(`desktop: ${message}`) });
    if (!this.state.store.readOnly || this.hostedConfig) {
      const registry = withHostedEndpoint(loadRegistry(this.state.store));
      saveRegistry(this.state.store, registry);
      const hosted = hostedGatewayUrl();
      if (hosted) policy.writeConfirmation(this.state.store, "ep_gideoncloud", {
        origin: hosted, scheme: "https", trust: "public", addresses: "",
      });
    }
    this.dialog = makeConnectDialog({ BrowserWindowCtor: this.electron.BrowserWindow, ipcMain: this.electron.ipcMain,
      handlers: this.handlers(), log: (message) => console.warn(message) });
    this.dialog.registerIpc();
  }

  target() { return this.state.activeUrl || this.hostedConfig?.hostedUrl || this.gateway.url; }
  registry() { return loadRegistry(this.state.store); }
  openDialog() { this.dialog?.open(alive(this.mainWindow()) ? this.mainWindow() : null); }

  async refresh() {
    if (this.state.store) this.state.health = await policy.probeAll(this.registry(), { localBaseUrl: this.gateway.url || "" });
    return this.state.health;
  }

  recordHealth(probe) {
    const id = this.registry().active;
    if (id) this.state.health = { ...this.state.health, [id]: probe };
  }

  cancelReachability() {
    if (this.state.timer !== null) clearTimeout(this.state.timer);
    Object.assign(this.state, { timer: null, attempt: 0, epoch: this.state.epoch + 1 });
  }

  scheduleReachability(window, delay = 0) {
    const address = this.state.activeUrl;
    if (!address || address === this.gateway.url) return;
    if (this.state.timer !== null) clearTimeout(this.state.timer);
    const epoch = this.state.epoch;
    this.state.timer = setTimeout(async () => {
      this.state.timer = null;
      if (!alive(window) || epoch !== this.state.epoch || address !== this.state.activeUrl) return;
      const probe = await policy.probeEndpoint(address);
      if (!alive(window) || epoch !== this.state.epoch || address !== this.state.activeUrl) return;
      this.recordHealth(probe);
      const step = policy.nextReconnectStep({ status: probe.status, attempt: this.state.attempt });
      if (step.action === "retry") {
        this.state.attempt = step.attempt;
        console.log(`gateway ${address} ${probe.status}; re-checking in ${step.delayMs}ms (attempt ${step.attempt})`);
        this.scheduleReachability(window, step.delayMs);
        return;
      }
      this.cancelReachability();
      if (step.action === "stay") { window.webContents.loadURL(address); return; }
      console.warn(`gateway ${address} ${probe.status}: ${step.reason} — stopping automatic re-checks`);
      this.openDialog();
    }, delay);
  }

  async navigate(window, address, local = false) {
    if (!alive(window)) return false;
    this.cancelReachability();
    this.state.activeUrl = address;
    const epoch = this.state.epoch;
    const attachBridge = !this.hostedConfig && policy.shouldAttachBridge(address);
    if (this.workspace.hasBridge(window) !== attachBridge) this.workspace.mount(window, { attachBridge });
    const page = window.webContents;
    page.loadFile(path.join(__dirname, "../../views/loading.html"));
    try {
      await (local ? this.gateway.waitReady(window) : waitRemote(window, address));
      if (!alive(window) || epoch !== this.state.epoch) return false;
      if (!local && address !== hostedGatewayUrl()) page.once("did-finish-load", () => this.discoverName(page, address));
      page.loadURL(address);
      return true;
    } catch (error) {
      if (!alive(window) || epoch !== this.state.epoch) return false;
      if (error.probe) this.recordHealth(error.probe);
      console.warn(`could not reach ${address}: ${error.message}`);
      if (this.hostedConfig && address === this.hostedConfig.hostedUrl) {
        const { response } = await this.electron.dialog.showMessageBox(window, { type: "error", title: "Gideon",
          message: "Could not connect to Gideon Cloud.", detail: "Check your connection and try again, or open your saved gateways.",
          buttons: ["Retry", "Gateways", "Quit"] });
        if (response === 0) return this.navigate(window, address);
        if (response === 1) this.openDialog();
        if (response === 2) this.quit();
        return false;
      }
      if (!this.gateway.url || address === this.gateway.url) return false;
      this.openDialog();
      return this.navigate(window, this.gateway.url, true);
    }
  }

  async discoverName(page, address) {
    if (!page || page.isDestroyed?.() || !this.state.store) return;
    const row = this.registry().endpoints.find((endpoint) => endpoint.base_url === address && endpoint.id !== policy.LOCAL_ENDPOINT_ID);
    if (!row) return;
    try {
      const name = await page.executeJavaScript(`(async () => {
        try {
          const response = await fetch('/api/companion/discovery', { credentials: 'same-origin' });
          if (!response.ok) return '';
          const payload = await response.json();
          return typeof payload?.instance_name === 'string' ? payload.instance_name : '';
        } catch { return ''; }
      })()`);
      const result = policy.adoptGatewayLabel(this.state.store, row.id, name);
      if (result.changed) console.log(`gateway ${row.id} named itself "${result.label}"`);
    } catch (error) { console.warn(`could not read the gateway's instance name: ${error.message}`); }
  }

  async fingerprint(row) {
    return row && row.kind !== "local" ? await policy.currentFingerprintFor(row.base_url) : "";
  }

  async chooseHostedStartup() {
    const registry = this.registry();
    const row = registry.endpoints.find((endpoint) => endpoint.id === registry.active);
    const currentFingerprint = row?.id === HOSTED_ENDPOINT_ID ? "" : await this.fingerprint(row);
    const choice = row?.kind === "remote" ? policy.switchTo(this.state.store, row.id,
      { localBaseUrl: "", currentFingerprint }) : { ok: false };
    if (choice.ok) return choice.navigateTo;
    const hosted = registry.endpoints.find((endpoint) => endpoint.id === HOSTED_ENDPOINT_ID);
    if (!hosted) throw new Error("Configured Gideon Cloud endpoint is unavailable");
    saveRegistry(this.state.store, setActive(registry, HOSTED_ENDPOINT_ID));
    return hosted.base_url;
  }

  async chooseStartup() {
    if (this.hostedConfig) {
      await this.navigate(this.mainWindow(), await this.chooseHostedStartup());
      this.refresh().catch(() => {});
      return;
    }
    if (this.gateway.url) policy.rememberLocalGateway(this.state.store, this.gateway.url);
    const registry = this.registry();
    const row = registry.endpoints.find((endpoint) => endpoint.id === registry.active);
    const currentFingerprint = await this.fingerprint(row);
    const choice = policy.describeStartup({ store: this.state.store, currentFingerprint });
    this.state.warnings = choice.warnings || [];
    if (choice.mode === "connect") {
      console.log(`connecting to a paired gateway: ${choice.origin} (${choice.trust})`);
      await this.navigate(this.mainWindow(), choice.origin);
    } else {
      console.log(`starting in spawn-local mode (${choice.reason})`);
      await this.loadLocal(this.mainWindow());
    }
    if (this.state.warnings.length) this.openDialog();
    this.refresh().catch(() => {});
  }

  async loadLocal(window) {
    while (alive(window)) {
      const page = window.webContents;
      page.loadFile(path.join(__dirname, "../../views/loading.html"));
      window.show();
      try {
        await this.gateway.waitReady(window);
        if (!alive(window)) return;
        this.state.activeUrl = this.gateway.url;
        page.loadURL(this.gateway.url);
        return;
      } catch {
        if (!alive(window)) return;
        const { response } = await this.electron.dialog.showMessageBox(window, { type: "error", title: "Gideon",
          message: "Could not connect to the Gideon backend.", detail: "The gateway failed to start. Try reopening the app.", buttons: ["Retry", "Quit"] });
        if (response === 0) continue;
        if (window === this.mainWindow()) this.quit();
        else window.destroy();
        return;
      }
    }
  }

  async confirm({ input, label }) {
    const plan = await policy.prepareEndpoint(input);
    if (!plan.ok) return plan;
    const { id } = policy.confirmEndpoint(this.state.store, plan, { label });
    this.state.warnings = [];
    const local = plan.trust === "loopback" && plan.navigateTo === this.gateway.url;
    const ok = await this.navigate(this.mainWindow(), plan.navigateTo, local);
    if (ok) this.dialog.close();
    return { ok, id, origin: plan.origin, navigateTo: plan.navigateTo };
  }

  async select(id) {
    const row = this.registry().endpoints.find((endpoint) => endpoint.id === id);
    const currentFingerprint = await this.fingerprint(row);
    const result = policy.switchTo(this.state.store, id, { localBaseUrl: this.gateway.url || "", currentFingerprint });
    if (!result.ok) return { ok: false, code: result.reason, message: result.needsConfirmation
      ? "This gateway needs to be confirmed again before the app will connect to it."
      : `That gateway could not be opened (${result.reason}).` };
    this.state.warnings = [];
    const ok = await this.navigate(this.mainWindow(), result.navigateTo, isLocalRow(result.endpoint));
    if (ok) this.dialog.close();
    return { ok, id };
  }

  handlers() {
    return {
      list: async () => {
        const { store, warnings, health } = this.state;
        const registry = this.registry();
        return describeList({ registry, activeId: registry.active, health, localBaseUrl: this.gateway.url || "", warnings,
          storeStatus: store.status, storeSafe: Boolean(store.permissions?.safe), readOnly: store.readOnly });
      },
      prepare: (input) => policy.prepareEndpoint(input), confirm: (input) => this.confirm(input), switchTo: (id) => this.select(id),
      forget: async (id) => {
        const result = policy.forgetEndpoint(this.state.store, id);
        if (result.ok) delete this.state.health[id];
        return result;
      },
      refresh: async () => { await this.refresh(); return { ok: true }; },
    };
  }
}

module.exports = { EndpointSession, waitRemote, isLocalRow };
