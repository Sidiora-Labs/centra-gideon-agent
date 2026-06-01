const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");
const { findGideonBin } = require("../find-bin");

const HOME = "/mock/home";
const RESOURCES = "/mock/resources";
const DIRNAME = "/mock/electron";

const fakeOs = { homedir: () => HOME };

const only = (target) => ({
  accessSync: (p) => { if (p !== target) throw new Error("ENOENT"); },
  constants: { X_OK: fs.constants.X_OK },
});

const none = {
  accessSync: () => { throw new Error("ENOENT"); },
  constants: { X_OK: fs.constants.X_OK },
};

describe("findGideonBin", () => {
  it("returns bundled path when it exists", () => {
    const bundled = path.join(RESOURCES, "backend-dist", "gideon-backend", "gideon-backend");
    const fakeFs = only(bundled);
    const result = findGideonBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, bundled);
  });

  it("returns ~/.local/bin/gideon when bundled paths don't exist", () => {
    const localBin = path.join(HOME, ".local", "bin", "gideon");
    const fakeFs = only(localBin);
    const result = findGideonBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, localBin);
  });

  it("returns ~/.gideon-app/.venv/bin/gideon when only venv binary exists", () => {
    const venvBin = path.join(HOME, ".gideon-app", ".venv", "bin", "gideon");
    const fakeFs = only(venvBin);
    const result = findGideonBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, venvBin);
  });

  it("returns ../bin/gideon relative to dirname when only that path exists", () => {
    const binPath = path.resolve(DIRNAME, "..", "bin", "gideon");
    const fakeFs = only(binPath);
    const result = findGideonBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, binPath);
  });

  it("falls back to bare 'gideon' when no candidates are executable", () => {
    const result = findGideonBin(none, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, "gideon");
  });

  it("returns first match when multiple candidates exist", () => {
    const bundled = path.join(RESOURCES, "backend-dist", "gideon-backend", "gideon-backend");
    const localBin = path.join(HOME, ".local", "bin", "gideon");
    const fakeFs = {
      accessSync: (p) => { if (p !== bundled && p !== localBin) throw new Error("ENOENT"); },
      constants: { X_OK: fs.constants.X_OK },
    };
    const result = findGideonBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, bundled);
  });

  it("handles resourcesPath being undefined", () => {
    const localBin = path.join(HOME, ".local", "bin", "gideon");
    const fakeFs = only(localBin);
    const result = findGideonBin(fakeFs, fakeOs, path, undefined, DIRNAME);
    assert.equal(result, localBin);
  });

  it("resolves dirname-relative dev path correctly", () => {
    const devBin = path.resolve(DIRNAME, "backend-dist", "gideon-backend", "gideon-backend");
    const fakeFs = only(devBin);
    const result = findGideonBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, devBin);
  });

  it("skips candidates that throw non-ENOENT errors (e.g. EACCES)", () => {
    const venvBin = path.join(HOME, ".gideon-app", ".venv", "bin", "gideon");
    const fakeFs = {
      accessSync: (p) => { if (p !== venvBin) throw new Error("EACCES"); },
      constants: { X_OK: fs.constants.X_OK },
    };
    const result = findGideonBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, venvBin);
  });
});
