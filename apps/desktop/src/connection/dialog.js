"use strict";

const path = require("node:path");
const { LOCAL_ENDPOINT_ID, HEALTH_UNKNOWN } = require("./controller");
const CONNECT_CHANNELS = Object.freeze(Object.fromEntries(
  ["list", "prepare", "confirm", "switch", "forget", "refresh"].map((name) => [name, `gideon:connect:${name}`])));
const HEALTH_COPY = {
  reachable: ["ok", "Reachable"], unreachable: ["warn", "Not answering"], timeout: ["warn", "Timed out"],
  needs_pairing: ["act", "Needs pairing again", "pair"], not_a_gateway: ["bad", "Answered, but not a Gideon gateway"],
  redirected: ["bad", "Redirected somewhere else — not followed"], http_error: ["warn", "Answered with an error"],
  refused_by_policy: ["bad", "Address the app will not connect to"], unknown: ["idle", "Not checked yet"],
};

function healthCopy(status) {
  const [tone, text, action = ""] = HEALTH_COPY[status] || HEALTH_COPY.unknown;
  return { tone, text, action };
}

function describeRow(row, { activeId, health = {}, localBaseUrl = "" } = {}) {
  const native = row.id === LOCAL_ENDPOINT_ID || row.kind === "local";
  const url = (native ? localBaseUrl || row.base_url : row.base_url) || "";
  const status = health[row.id]?.status || HEALTH_UNKNOWN;
  const presentation = healthCopy(status);
  return { id: row.id, label: row.label || url || row.id, url, kind: native ? "local" : "remote",
    active: row.id === activeId, removable: !native, status, statusText: presentation.text,
    statusTone: presentation.tone, statusAction: presentation.action, version: health[row.id]?.version || "", missingUrl: !url };
}

function describeList({ registry, activeId, health, localBaseUrl, warnings = [], storeStatus = "", storeSafe = true, readOnly = false }) {
  const selected = activeId || registry.active || "";
  return { activeId: selected, rows: (registry.endpoints || []).map((row) => describeRow(row, { activeId: selected, health, localBaseUrl })),
    warnings, storeStatus, storeSafe, readOnly };
}

function makeConnectDialog({ BrowserWindowCtor, ipcMain, handlers, log = () => {} }) {
  let window = null;
  const read = (argument, key) => String(argument?.[key] || "");
  const operations = {
    list: () => handlers.list(), refresh: () => handlers.refresh(),
    prepare: (argument) => handlers.prepare(read(argument, "input")),
    confirm: (argument) => handlers.confirm({ input: read(argument, "input"), label: read(argument, "label") }),
    switch: (argument) => handlers.switchTo(read(argument, "id")),
    forget: (argument) => handlers.forget(read(argument, "id")),
  };
  return {
    get window() { return window; },
    open(parent) {
      if (window && !window.isDestroyed()) { window.focus(); return window; }
      window = new BrowserWindowCtor({ width: 600, height: 620, resizable: true, minimizable: false,
        useContentSize: true, parent: parent || undefined, modal: Boolean(parent), title: "Gateways", backgroundColor: "#0a1320",
        webPreferences: { preload: path.join(__dirname, "../bridge/connection-preload.js"),
          contextIsolation: true, nodeIntegration: false, sandbox: false } });
      window.setMenu?.(null);
      window.loadFile(path.join(__dirname, "../../views/connection.html"));
      window.on("closed", () => { window = null; });
      return window;
    },
    close() { if (window && !window.isDestroyed()) window.close(); window = null; },
    registerIpc(ipc = ipcMain) {
      for (const [name, operation] of Object.entries(operations)) {
        ipc.handle(CONNECT_CHANNELS[name], async (_event, argument) => {
          try { return await operation(argument); }
          catch (error) {
            log(`connect dialog ${name} failed: ${error?.message}`);
            return { ok: false, code: "INTERNAL", message: "That did not work. See the app log." };
          }
        });
      }
    },
  };
}

module.exports = { CONNECT_CHANNELS, healthCopy, describeRow, describeList, makeConnectDialog };
