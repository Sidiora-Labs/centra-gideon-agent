"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const test = require("node:test");
const { LocalGateway } = require("../src/application/local-gateway");

const ROOT = path.resolve(__dirname, "../../..");
const RUNTIME = String.raw`
import asyncio
import os
import signal
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from gideon.interfaces.dashboard.handlers.hypermid import register_hypermid_routes
from checks.hypermid.waves.local_journey import lifecycle

async def main():
    host = lifecycle(Path(os.environ["HYPERMID_TEST_ROOT"]))
    status = await host.start()
    assert status.available and status.healthy, status.to_dict()
    app = web.Application()
    app["state"] = SimpleNamespace(hypermid=host)
    register_hypermid_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        loop.add_signal_handler(getattr(signal, name), stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        await host.stop()

asyncio.run(main())
`;

function lineFrom(stream) {
  return new Promise((resolve, reject) => {
    let buffer = "";
    const receive = (chunk) => {
      buffer += chunk.toString();
      const end = buffer.indexOf("\n");
      if (end < 0) return;
      stream.off("data", receive);
      resolve(buffer.slice(0, end));
    };
    stream.on("data", receive);
    stream.on("error", reject);
  });
}

test("desktop binds the packaged daemon and reports authenticated runtime health", async (context) => {
  const sourceDaemon = process.env.HYPERMID_DAEMON_BINARY;
  assert.ok(sourceDaemon && fs.statSync(sourceDaemon).isFile(), "HYPERMID_DAEMON_BINARY must identify the built real daemon");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "gideon-hypermid-desktop-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const resources = path.join(root, "resources");
  const executable = path.join(resources, "backend-dist", "gideon-backend", "_internal", "gideon", "hypermid", "bin", "hypermid-daemon");
  fs.mkdirSync(path.dirname(executable), { recursive: true });
  fs.copyFileSync(sourceDaemon, executable);
  fs.chmodSync(executable, 0o755);

  const python = process.env.HYPERMID_TEST_PYTHON || "python";
  const runtime = spawn(python, ["-c", RUNTIME], { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, PYTHONPATH: "runtime", HYPERMID_DAEMON_BINARY: executable,
      HYPERMID_TEST_ROOT: path.join(root, "runtime") } });
  let errors = "";
  runtime.stderr.on("data", (chunk) => { errors += chunk.toString(); });
  context.after(async () => {
    if (runtime.exitCode === null) runtime.kill("SIGTERM");
    await new Promise((resolve) => runtime.once("exit", resolve));
  });
  const first = await Promise.race([
    lineFrom(runtime.stdout),
    new Promise((_, reject) => runtime.once("exit", (code) => reject(new Error(`runtime exited ${code}: ${errors}`)))),
  ]);
  const port = Number(first);
  assert.ok(Number.isInteger(port) && port > 0, `invalid runtime port ${first}`);

  const statuses = [];
  const gateway = new LocalGateway({ app: { isPackaged: true }, home: path.join(root, "home"),
    status: (message) => statuses.push(message), log: { log() {}, warn() {}, error() {} } });
  gateway.hypermid.resourcesPath = resources;
  gateway.url = `http://127.0.0.1:${port}`;
  const environment = gateway.hypermid.gatewayEnvironment();
  assert.equal(environment.GIDEON_HOME, path.join(root, "home"));
  assert.equal(environment.GIDEON_HYPERMID_DAEMON, executable);

  const overview = await gateway.hypermid.refresh();
  assert.equal(overview.healthy, true);
  assert.equal(overview.availability, "available");
  assert.equal(overview.versions.protocol, "hypermid.v1");
  assert.ok(overview.versions.storage && overview.versions.build);
  assert.match(statuses.at(-1), /Hypermid healthy hypermid\.v1/);
});
