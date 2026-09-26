"use strict";

const fs = require("node:fs");
const path = require("node:path");

function isHostedWindowsMode({ platform = process.platform, isPackaged = false } = {}) {
  return platform === "win32" && isPackaged === true;
}

function validateHostedOrigin(value) {
  if (typeof value !== "string" || !value || value !== value.trim()) {
    throw new Error("hostedUrl must be an HTTPS origin");
  }
  let url;
  try { url = new URL(value); }
  catch { throw new Error("hostedUrl must be an HTTPS origin"); }
  if (url.protocol !== "https:" || !url.hostname || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash || value !== url.origin) {
    throw new Error("hostedUrl must be an HTTPS origin without credentials, path, query, or hash");
  }
  return url.origin;
}

function readHostedConfig({ platform = process.platform, isPackaged = false,
  resourcesPath = process.resourcesPath, fsMod = fs } = {}) {
  if (!isHostedWindowsMode({ platform, isPackaged })) return null;
  if (!resourcesPath) throw new Error("Hosted Windows configuration is missing: Electron resources path is unavailable");
  const file = path.join(resourcesPath, "hosted-config.json");
  let config;
  try { config = JSON.parse(fsMod.readFileSync(file, "utf8")); }
  catch (error) { throw new Error(`Hosted Windows configuration could not be read at ${file}: ${error.message}`); }
  if (!config || Array.isArray(config) || config.schemaVersion !== 1 || config.mode !== "hosted") {
    throw new Error(`Hosted Windows configuration is invalid at ${file}: expected schemaVersion 1 and mode hosted`);
  }
  try { validateHostedOrigin(config.hostedUrl); }
  catch (error) { throw new Error(`Hosted Windows configuration is invalid at ${file}: ${error.message}`); }
  return { schemaVersion: 1, mode: "hosted", hostedUrl: config.hostedUrl };
}

module.exports = { isHostedWindowsMode, validateHostedOrigin, readHostedConfig };
