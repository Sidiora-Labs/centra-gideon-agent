"use strict";

const { contextBridge, ipcRenderer } = require("electron");
const CAPABILITIES = ["audio_capture", "global_hotkey", "native_notifications", "tray", "screen_capture", "login_item", "system_audio"];
const IPC_CHANNELS = Object.fromEntries(Object.entries({ probe: "probe", request: "request", snapshot: "snapshot", state: "state",
  hotkeyBind: "hotkey-bind", capturing: "capturing", pushToTalk: "push-to-talk", loginItemGet: "login-item-get",
  loginItemSet: "login-item-set", notify: "notify", notificationActivate: "notification-activate" })
  .map(([key, name]) => [key, "gideon-desktop:" + name]));

function subscribe(channel, callback, select = (payload) => payload) {
  if (typeof callback !== "function") return () => {};
  const listener = (_event, payload) => {
    const value = select(payload);
    if (value !== undefined) callback(value);
  };
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

const known = (capability) => typeof capability === "string" && CAPABILITIES.includes(capability);
const reason = (capability) => `unknown capability: ${String(capability)}`;

contextBridge.exposeInMainWorld("gideonDesktop", {
  onStatus(callback) { return subscribe("status", callback); },
  capabilities: {
    names() { return Array.from(CAPABILITIES); },
    probe(capability) {
      return known(capability) ? ipcRenderer.invoke(IPC_CHANNELS.probe, capability)
        : Promise.resolve({ available: false, granted: "unavailable", requestable: false, reason: reason(capability) });
    },
    snapshot() { return ipcRenderer.invoke(IPC_CHANNELS.snapshot); },
    request(capability) {
      return known(capability) ? ipcRenderer.invoke(IPC_CHANNELS.request, capability)
        : Promise.resolve({ granted: false, state: "unavailable", prompted: false, reason: reason(capability) });
    },
    on(capability, callback) {
      return known(capability) ? subscribe(IPC_CHANNELS.state, callback,
        (payload) => payload?.capability === capability ? payload.state : undefined) : () => {};
    },
  },
  pushToTalk: {
    bind(chord) { return ipcRenderer.invoke(IPC_CHANNELS.hotkeyBind, String(chord ?? "")); },
    setCapturing(active) { return ipcRenderer.invoke(IPC_CHANNELS.capturing, Boolean(active)); },
    on(callback) { return subscribe(IPC_CHANNELS.pushToTalk, callback); },
  },
  loginItem: {
    get() { return ipcRenderer.invoke(IPC_CHANNELS.loginItemGet); },
    set(enabled) { return ipcRenderer.invoke(IPC_CHANNELS.loginItemSet, Boolean(enabled)); },
  },
  notifications: {
    show(note) {
      const payload = Object.fromEntries(["title", "body", "route"].map((key) => [key, String(note?.[key] ?? "")]));
      return ipcRenderer.invoke(IPC_CHANNELS.notify, payload);
    },
    on(callback) { return subscribe(IPC_CHANNELS.notificationActivate, callback); },
  },
});
