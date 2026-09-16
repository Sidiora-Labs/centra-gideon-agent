"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { spawn, execFileSync } = require("node:child_process");
const { findGideonBin } = require("../gateway/executable");
const { buildGatewayEnv } = require("../gateway/environment");
const { createLoginPathResolver } = require("../gateway/login-path");
const { shutdownGateway } = require("../gateway/shutdown");
const { assertLoopbackTarget } = require("../connection/controller");

const READY_PREFIX = "GIDEON_READY:";
const START_TIMEOUT = 120000;
const pause = (duration) => new Promise((resolve) => setTimeout(resolve, duration));

function readyOrigin(line) {
  if (!line.startsWith(READY_PREFIX)) return null;
  try {
    const { port } = JSON.parse(line.slice(READY_PREFIX.length));
    const value = Number(port);
    return Number.isInteger(value) && value > 0 && value <= 65535 ? `http://localhost:${port}` : null;
  } catch { return null; }
}

class LocalGateway {
  #shellToken = null;

  constructor({ app, home, status, log = console }) {
    Object.assign(this, { app, home, status, log, child: null, url: null });
    this.loginPath = createLoginPathResolver({ env: process.env, execFileSync, warn: log.warn });
  }

  start() {
    try { fs.mkdirSync(this.home, { recursive: true, mode: 0o700 }); }
    catch (error) { this.log.warn("Failed to create gideon dir:", error.message); }
    const executable = findGideonBin(fs, os, path, process.resourcesPath, path.resolve(__dirname, "../.."));
    const arguments_ = ["gateway", "--port", "auto", "--json-ready", "--no-open"];
    this.status("Starting gateway…");
    this.log.log(`Starting gateway: ${executable} ${arguments_.join(" ")}`);
    return new Promise((resolve, reject) => {
      let output = "", finished = false;
      const complete = (error, origin) => {
        if (finished) return;
        finished = true;
        clearTimeout(deadline);
        if (error) return reject(error);
        this.url = origin;
        this.status("Connected ✓");
        resolve(origin);
      };
      const deadline = setTimeout(() => complete(new Error("Gateway start timed out")), START_TIMEOUT);
      try {
        this.child = spawn(executable, arguments_, { stdio: ["ignore", "pipe", "pipe"], detached: true,
          env: buildGatewayEnv({ env: process.env, loginPath: this.loginPath(),
            projectDir: this.app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "../../../..") }) });
      } catch (error) { complete(error); return; }
      const child = this.child;
      child.stdout.on("data", (chunk) => {
        const lines = (output + chunk.toString()).split("\n");
        output = lines.pop();
        for (const line of lines) {
          const origin = readyOrigin(line);
          if (origin) complete(null, origin);
        }
      });
      child.stderr.on("data", (chunk) => this.log.error("gateway:", chunk.toString().trim()));
      child.on("error", (error) => { this.log.error("Failed to start gateway:", error.message); complete(error); });
      child.on("exit", (code) => {
        if (this.child === child) this.child = null;
        this.log.log(`Gateway exited with code ${code}`);
        complete(new Error(`Gateway exited with code ${code}`));
      });
    });
  }

  async stop() {
    const child = this.child;
    this.child = null;
    const outcome = await shutdownGateway({ child, graceMs: 8000, killGroup: true,
      log: (message) => this.log.log(`gateway shutdown: ${message}`) });
    this.log.log(`Gateway shutdown outcome: ${outcome.outcome}${outcome.groupSwept ? " (residual process-group members were killed)" : ""}`);
    return outcome;
  }

  request(method, route, body, headers = {}, statusOnly = false) {
    let target;
    try {
      if (!this.url) return Promise.resolve(null);
      assertLoopbackTarget(this.url, `${method} ${route}`);
      target = new URL(route, this.url);
      assertLoopbackTarget(target.href, `${method} ${route}`);
    } catch (error) { this.log.warn(`desktop: ${error.message}`); return Promise.resolve(null); }
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body));
    return new Promise((resolve) => {
      const options = { method, timeout: statusOnly ? 2000 : method === "POST" ? 5000 : 3000,
        headers: payload ? { "Content-Type": "application/json", "Content-Length": payload.length, ...headers } : headers };
      const request = http.request(target, options, (response) => {
        if (statusOnly) { response.resume(); resolve(response.statusCode); return; }
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("error", () => resolve(null));
        response.on("end", () => {
          if (response.statusCode !== 200) {
            if (method === "POST") this.log.warn(`desktop ${route} → HTTP ${response.statusCode}`);
            return resolve(null);
          }
          try { resolve(JSON.parse(Buffer.concat(chunks).toString())); }
          catch { resolve(null); }
        });
      });
      request.on("error", (error) => {
        if (method === "POST") this.log.warn(`desktop ${route} failed: ${error.message}`);
        resolve(null);
      });
      request.on("timeout", () => { request.destroy(); resolve(null); });
      request.end(payload || undefined);
    });
  }

  async register(capabilities) {
    let secret;
    try { secret = fs.readFileSync(path.join(this.home, ".local_secret"), "utf8").trim(); }
    catch { secret = ""; }
    if (!secret) { this.log.warn("desktop: no local secret; capabilities stay unregistered"); return false; }
    const manifest = { shell: { version: this.app.getVersion(), platform: process.platform }, capabilities };
    const result = await this.request("POST", "/api/desktop/register", manifest, { "X-Local-Secret": secret });
    if (!result?.shell_token) return false;
    this.#shellToken = result.shell_token;
    this.log.log("desktop: capability manifest registered with the gateway");
    return true;
  }

  publish(capabilities) {
    return this.#shellToken ? this.request("POST", "/api/desktop/state", { capabilities }, { "X-Shell-Token": this.#shellToken }) : Promise.resolve(null);
  }

  unregister() {
    const token = this.#shellToken;
    this.#shellToken = null;
    return token ? this.request("POST", "/api/desktop/unregister", {}, { "X-Shell-Token": token }) : Promise.resolve(null);
  }

  async waitReady(window) {
    const deadline = Date.now() + START_TIMEOUT;
    while (true) {
      if (window?.isDestroyed()) throw new Error("Window closed");
      if (Date.now() > deadline) throw new Error("Backend timeout");
      const status = await this.request("GET", "/api/status", undefined, {}, true);
      if (status !== null && status < 500) return;
      await pause(500);
    }
  }
}

module.exports = { LocalGateway, readyOrigin, START_TIMEOUT, pause };
