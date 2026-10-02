"use strict";

const fs = require("node:fs");
const path = require("node:path");

const OVERVIEW_ROUTE = "/api/hypermid/overview";

function executableName(platform = process.platform) {
  return platform === "win32" ? "hypermid-daemon.exe" : "hypermid-daemon";
}

function usableExecutable(candidate, fileSystem = fs, platform = process.platform) {
  try {
    fileSystem.accessSync(candidate, platform === "win32" ? fileSystem.constants.F_OK : fileSystem.constants.X_OK);
    return true;
  } catch { return false; }
}

function installationPaths({ resourcesPath = "", projectDir = "", platform = process.platform } = {}) {
  const executable = executableName(platform);
  return [
    path.join(resourcesPath, "backend-dist", "gideon-backend", "_internal", "gideon", "hypermid", "bin", executable),
    path.join(resourcesPath, "backend-dist", "gideon-backend", "gideon", "hypermid", "bin", executable),
    path.join(projectDir, "runtime", "gideon", "hypermid", "bin", executable),
  ].filter((candidate, index, candidates) => candidate && candidates.indexOf(candidate) === index);
}

function normalizeOverview(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const daemon = value.daemon && typeof value.daemon === "object" ? value.daemon : {};
  const versions = value.versions && typeof value.versions === "object" ? value.versions : {};
  const availability = String(value.availability || "unavailable");
  const health = String(daemon.health || "unavailable");
  return {
    availability,
    healthy: availability === "available" && health === "healthy",
    mode: String(value.mode || "off"),
    daemon: { state: String(daemon.state || "unknown"), health },
    versions: {
      protocol: versions.protocol == null ? null : String(versions.protocol),
      storage: versions.storage == null ? null : String(versions.storage),
      build: versions.build == null ? null : String(versions.build),
    },
    store_health: String(value.store_health || "unknown"),
    checked_at_ms: Number.isSafeInteger(value.checked_at_ms) && value.checked_at_ms >= 0 ? value.checked_at_ms : 0,
    failure_code: value.failure_code == null ? null : String(value.failure_code).slice(0, 64),
  };
}

class HypermidDesktop {
  constructor({ home, resourcesPath, projectDir, platform = process.platform, isPackaged = false,
    request, status = () => {}, fileSystem = fs } = {}) {
    this.home = path.resolve(String(home || ""));
    this.resourcesPath = String(resourcesPath || "");
    this.projectDir = String(projectDir || "");
    this.platform = platform;
    this.isPackaged = Boolean(isPackaged);
    this.request = request;
    this.status = status;
    this.fileSystem = fileSystem;
    this.last = null;
  }

  installation() {
    const candidates = installationPaths(this);
    const executable = candidates.find((candidate) => usableExecutable(candidate, this.fileSystem, this.platform)) || null;
    return { available: executable !== null, executable, root: this.home,
      source: executable ? (this.isPackaged ? "packaged" : "development") : "runtime-discovery" };
  }

  gatewayEnvironment() {
    const installation = this.installation();
    const environment = { GIDEON_HOME: installation.root };
    if (installation.executable) environment.GIDEON_HYPERMID_DAEMON = installation.executable;
    this.status(installation.available ? "Starting gateway with packaged Hypermid…" : "Starting gateway; Hypermid install will be checked by the runtime…");
    return environment;
  }

  async refresh() {
    const overview = normalizeOverview(await this.request?.("GET", OVERVIEW_ROUTE));
    this.last = overview;
    if (!overview) {
      this.status("Connected ✓ · Hypermid status unavailable");
      return null;
    }
    const version = overview.versions.protocol ? ` ${overview.versions.protocol}` : "";
    const failure = overview.failure_code ? ` · ${overview.failure_code}` : "";
    const state = overview.healthy ? `healthy${version}` : `${overview.availability} (${overview.daemon.health})${failure}`;
    this.status(`Connected ✓ · Hypermid ${state}`);
    return overview;
  }

  stopped() {
    this.last = null;
    this.status("Gateway stopped · Hypermid runtime retired");
  }
}

module.exports = { HypermidDesktop, OVERVIEW_ROUTE, executableName, installationPaths, normalizeOverview, usableExecutable };
