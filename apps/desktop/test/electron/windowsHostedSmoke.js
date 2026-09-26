"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { spawn } = require("node:child_process");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(label, probe, timeout = 60000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try { const value = await probe(); if (value) return value; }
    catch (error) { last = error; }
    await delay(500);
  }
  throw new Error(`Timed out waiting for ${label}${last ? `: ${last.message}` : ""}`);
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function hostedTarget(port, origin) {
  const response = await fetch(`http://127.0.0.1:${port}/json/list`);
  assert.equal(response.ok, true, "Electron CDP endpoint unavailable");
  return (await response.json()).find((target) => target.type === "page" &&
    target.url.startsWith(origin) && target.webSocketDebuggerUrl);
}

function connect(target) {
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  let sequence = 0;
  const pending = new Map();
  socket.addEventListener("message", ({ data }) => {
    const result = JSON.parse(data);
    const waiter = pending.get(result.id);
    if (!waiter) return;
    pending.delete(result.id);
    if (result.error) waiter.reject(new Error(result.error.message));
    else waiter.resolve(result.result);
  });
  socket.addEventListener("close", () => {
    for (const waiter of pending.values()) waiter.reject(new Error("CDP connection closed"));
    pending.clear();
  });
  const opened = new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  return {
    async evaluate(expression) {
      await opened;
      const id = ++sequence;
      const reply = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
      socket.send(JSON.stringify({ id, method: "Runtime.evaluate", params: { expression, returnByValue: true, awaitPromise: true } }));
      const result = await reply;
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
      return result.result.value;
    },
    close() { socket.close(); },
  };
}

async function launch(executable, profile, origin) {
  const port = await freePort();
  const child = spawn(executable, [`--remote-debugging-port=${port}`, `--user-data-dir=${profile}`],
    { stdio: "ignore", windowsHide: false, env: { ...process.env, GIDEON_CLOUD_URL: "" } });
  const target = await until("installed Electron hosted view", async () => {
    if (child.exitCode !== null) throw new Error(`Gideon exited ${child.exitCode}`);
    return hostedTarget(port, origin);
  }, 90000);
  return { child, cdp: connect(target) };
}

async function stop(instance) {
  instance.cdp.close();
  if (instance.child.exitCode !== null) return;
  const killer = spawn("taskkill", ["/PID", String(instance.child.pid), "/T", "/F"], { stdio: "ignore" });
  await new Promise((resolve) => killer.on("exit", resolve));
  await until("desktop exit", () => instance.child.exitCode !== null, 10000);
}

async function assertBoundary(cdp, origin) {
  const state = await cdp.evaluate(`({url:location.href,bridge:typeof window.gideonDesktop,
    requireType:typeof window.require,processType:typeof window.process})`);
  assert.equal(new URL(state.url).origin, origin);
  assert.equal(state.bridge, "undefined", "remote hosted view has privileged bridge");
  assert.equal(state.requireType, "undefined", "remote hosted view has Node require");
  assert.equal(state.processType, "undefined", "remote hosted view has Node process");
  return state.url;
}

async function smoke({ executable, url, email, password, reportDir }) {
  const parsed = new URL(url);
  const origin = parsed.origin;
  assert.equal(parsed.href, `${origin}/`, "hosted URL must be a bare HTTPS origin");
  assert.equal(parsed.protocol, "https:");
  assert.ok(email && password, "configured smoke sign-in credentials required");
  const profile = path.join(reportDir, "desktop-profile");
  fs.mkdirSync(profile, { recursive: true });
  let app;
  try {
    app = await launch(executable, profile, origin);
    await until("hosted sign-in", () => app.cdp.evaluate(`Boolean(document.querySelector('input[name="email"]') && document.querySelector('input[name="password"]'))`));
    await assertBoundary(app.cdp, origin);
    await app.cdp.evaluate(`location.hash = '#/projects'; true`);
    const credentials = JSON.stringify({ email, password });
    await app.cdp.evaluate(`(() => {
      const values = ${credentials};
      for (const name of ['email', 'password']) {
        const input = document.querySelector('input[name="' + name + '"]');
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        setter.call(input, values[name]);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
      document.querySelector('form button[type="submit"]').click();
      return true;
    })()`);
    await until("sign-in return to Projects", async () => app.cdp.evaluate(`
      location.hash === '#/projects' && !document.querySelector('input[name="email"]') &&
      Boolean(document.querySelector('main, [role="main"]')) &&
      document.body.innerText.includes('Projects')`), 120000);
    await assertBoundary(app.cdp, origin);
    await app.cdp.evaluate(`document.querySelector('nav button[aria-label^="Everything"]')?.click(); true`);
    await until("Files navigation control", () => app.cdp.evaluate(`Boolean(document.querySelector('nav button[aria-label="Files"]'))`));
    assert.equal(await app.cdp.evaluate(`(() => {
      const button = document.querySelector('nav button[aria-label="Files"]');
      if (!button) return false;
      button.click();
      return true;
    })()`), true);
    await until("hosted Files navigation", () => app.cdp.evaluate(`location.hash === '#/files' &&
      Boolean(document.body.innerText.match(/Files/i))`));
    await stop(app);
    app = await launch(executable, profile, origin);
    await until("reopened signed-in hosted page", () => app.cdp.evaluate(`
      !document.querySelector('input[name="email"]') &&
      Boolean(document.querySelector('.gideon-shell nav[data-tour="rail"]')) &&
      Boolean(document.querySelector('.gideon-workspace main, main.gideon-workspace'))`), 90000);
    const reopenedUrl = await assertBoundary(app.cdp, origin);
    const report = JSON.parse(fs.readFileSync(path.join(reportDir, "windows-proof.json"), "utf8"));
    Object.assign(report, { nativeSmoke: { signIn: "passed", returnRoute: "projects", navigation: "files",
      reopen: "passed", privilegedRemoteBridge: "absent", reopenedUrl } });
    fs.writeFileSync(path.join(reportDir, "windows-proof.json"), `${JSON.stringify(report, null, 2)}\n`);
    return report.nativeSmoke;
  } finally {
    if (app) await stop(app);
    fs.rmSync(profile, { recursive: true, force: true });
  }
}

module.exports = { until, freePort, hostedTarget, connect, assertBoundary, smoke };

if (require.main === module) {
  const [executable, url, reportDir] = process.argv.slice(2);
  smoke({ executable, url, reportDir, email: process.env.GIDEON_SMOKE_EMAIL,
    password: process.env.GIDEON_SMOKE_PASSWORD }).then(() => {
    console.log("Installed Windows Electron sign-in, return, navigation, reopen and boundary passed");
  }, (error) => { console.error(error); process.exitCode = 1; });
}
