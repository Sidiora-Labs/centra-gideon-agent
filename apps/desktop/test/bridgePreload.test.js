"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { Script } = require("node:vm");

const workspacePath = path.join(__dirname, "../src/application/window-workspace.js");
const workspace = fs.readFileSync(workspacePath, "utf8");

function preferences(view, attachBridge) {
  const expression = workspace.match(new RegExp(`const ${view} = new WebContentsView\\((\\{[\\s\\S]*?)\\);`));
  assert.ok(expression, `${view} must construct a WebContentsView`);
  return new Function("attachBridge", "path", "__dirname", `return (${expression[1]});`)(
    attachBridge, path, path.dirname(workspacePath),
  ).webPreferences;
}

test("the bridge-owning view loads its self-contained preload with the sandbox enabled", () => {
  const settings = preferences("content", true);
  assert.equal(settings.sandbox, true);
  assert.equal(settings.contextIsolation, true);
  assert.equal(settings.nodeIntegration, false);
  assert.equal(settings.preload, path.resolve(__dirname, "../src/bridge/dashboard-preload.js"));
  const preload = fs.readFileSync(settings.preload, "utf8");
  assert.doesNotThrow(() => new Script(preload, { filename: settings.preload }));
  assert.match(preload, /contextBridge\.exposeInMainWorld\("gideonDesktop"/);
  const dependencies = [...preload.matchAll(/require\(["']([^"']+)["']\)/g)].map((match) => match[1]);
  assert.deepEqual(dependencies, ["electron"]);
});

test("a dashboard without the bridge remains sandboxed", () => {
  const settings = preferences("content", false);
  assert.equal(settings.sandbox, true);
  assert.equal(settings.contextIsolation, true);
  assert.equal(settings.nodeIntegration, false);
  assert.equal(Object.hasOwn(settings, "preload"), false);
});

test("the separate drag view remains sandboxed without a preload", () => {
  for (const attachBridge of [true, false]) {
    const settings = preferences("drag", attachBridge);
    assert.equal(settings.sandbox, true);
    assert.equal(settings.contextIsolation, true);
    assert.equal(settings.nodeIntegration, false);
    assert.equal(Object.hasOwn(settings, "preload"), false);
  }
});
