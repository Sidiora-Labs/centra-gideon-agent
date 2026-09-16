"use strict";

const { contextBridge, ipcRenderer } = require("electron");
const { CONNECT_CHANNELS } = require("../connection/dialog");
const invoke = (operation, data) => ipcRenderer.invoke(CONNECT_CHANNELS[operation], data);
const text = (value) => String(value ?? "");

contextBridge.exposeInMainWorld("gideonConnect", {
  list() { return invoke("list"); },
  refresh() { return invoke("refresh"); },
  prepare(input) { return invoke("prepare", { input: text(input) }); },
  confirm(input, label) { return invoke("confirm", { input: text(input), label: text(label) }); },
  switchTo(id) { return invoke("switch", { id: text(id) }); },
  forget(id) { return invoke("forget", { id: text(id) }); },
});
