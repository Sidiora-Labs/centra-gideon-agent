"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { rememberGroup, retireLostGroup, liveGroup, exitDetail } = require("../src/gateway/lost");

test("unexpected gateway exit retires its actual surviving child group", { skip: process.platform !== "linux", timeout: 10000 }, async () => {
  const program = `const {spawn}=require('node:child_process');
    const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],{stdio:'ignore'});
    console.log(child.pid);setTimeout(()=>process.exit(7),500);`;
  const child = spawn(process.execPath, ["-e", program], { detached: true, stdio: ["ignore", "pipe", "pipe"] });
  let group;
  try {
    await once(child, "spawn");
    group = rememberGroup(child);
    assert.equal(group, child.pid);
    await once(child.stdout, "data");
    const [code] = await once(child, "exit");
    assert.equal(code, 7);
    assert.equal(liveGroup(group), true);
    assert.deepEqual(await retireLostGroup(group), { stopped: true });
    // Observe retirement before fallback cleanup can hide an orphan.
    assert.equal(liveGroup(group), false);
  } finally {
    if (group && liveGroup(group)) process.kill(-group, "SIGKILL");
    if (child.exitCode === null && !child.signalCode) child.kill("SIGKILL");
  }
});

test("loss evidence is bounded and unknown ownership refuses a restart", async () => {
  assert.deepEqual(await retireLostGroup(null), { stopped: false });
  const detail = exitDetail({ code: 1, stderr: "x".repeat(10000) });
  assert.ok(detail.length < 4100);
  assert.ok(detail.includes("exit 1"));
});
