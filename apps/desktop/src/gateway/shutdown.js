"use strict";

const DEFAULT_GRACE_MS = 8000;
const DEFAULT_KILL_GRACE_MS = 2000;
const DEFAULT_SWEEP_MS = 500;
const OUTCOMES = ["none", "exited", "killed", "unreachable", "orphaned"];

function defaultReadPgid(pid) {
  try {
    const output = require("node:child_process").execFileSync("ps", ["-p", String(pid), "-o", "pgid="], {
      encoding: "utf8", stdio: ["ignore", "pipe", "ignore"],
    });
    const group = Number.parseInt(output.trim(), 10);
    return Number.isInteger(group) && group > 0 ? group : null;
  } catch { return null; }
}

function shutdownGateway({ child, graceMs = DEFAULT_GRACE_MS, killGraceMs = DEFAULT_KILL_GRACE_MS,
  log = () => {}, setTimeoutFn = setTimeout, clearTimeoutFn = clearTimeout,
  killGroup = false, sweepMs = DEFAULT_SWEEP_MS, readPgid = defaultReadPgid,
  killPid = (pid, signal) => process.kill(pid, signal) } = {}) {
  const result = (outcome, code = null, signal = null, escalated = false, groupSwept = false) =>
    ({ outcome, code, signal, escalated, groupSwept });
  if (!child) return Promise.resolve(result("none"));
  if (child.exitCode != null) return Promise.resolve(result("none", child.exitCode));
  if (child.signalCode) return Promise.resolve(result("none", null, child.signalCode));
  const plausiblePid = Number.isInteger(child.pid) && child.pid > 1;
  const group = killGroup && plausiblePid && readPgid(child.pid) === child.pid;
  if (killGroup && !group) log(`group shutdown declined: pid ${child.pid} is not a verified group leader; its children may survive`);

  return new Promise((resolve) => {
    let phase = "terminating";
    let escalated = false;
    const timers = new Set();
    const clearTimers = () => { timers.forEach(clearTimeoutFn); timers.clear(); };
    const finish = (outcome, code = null, signal = null, swept = false) => {
      if (phase === "finished") return;
      phase = "finished";
      clearTimers();
      resolve(result(outcome, code, signal, escalated, swept));
    };
    const schedule = (callback, duration) => {
      if (phase === "finished") return;
      const timer = setTimeoutFn(callback, duration);
      timers.add(timer);
    };
    const deliver = (signal) => {
      try {
        if (group) killPid(-child.pid, signal);
        else child.kill(signal);
        return true;
      } catch (error) {
        log(`gateway ${signal} could not be delivered: ${error.message}`);
        finish("unreachable");
        return false;
      }
    };
    child.once("exit", (code, signal) => {
      if (phase === "finished") return;
      clearTimers();
      phase = "sweeping";
      log(`gateway exited during shutdown (code=${code} signal=${signal})`);
      const outcome = escalated ? "killed" : "exited";
      const complete = (swept = false) => finish(outcome, code ?? null, signal ?? null, swept);
      if (!group) return complete();
      try { killPid(-child.pid, 0); }
      catch { return complete(); }
      log("gateway process group still has members; scheduling a sweep");
      schedule(() => {
        try { killPid(-child.pid, "SIGKILL"); complete(true); }
        catch { complete(); }
      }, sweepMs);
    });
    log(group ? "asking the gateway and its process group to shut down (SIGTERM)…" : "asking the gateway to shut down (SIGTERM)…");
    if (!deliver("SIGTERM") || phase !== "terminating") return;
    schedule(() => {
      if (phase !== "terminating") return;
      phase = "killing";
      escalated = true;
      log(`gateway did not exit within ${graceMs}ms — escalating to SIGKILL`);
      if (!deliver("SIGKILL") || phase !== "killing") return;
      schedule(() => {
        log("gateway did not exit after SIGKILL — it may be orphaned");
        finish("orphaned");
      }, killGraceMs);
    }, graceMs);
  });
}

module.exports = { shutdownGateway, OUTCOMES, DEFAULT_GRACE_MS, DEFAULT_KILL_GRACE_MS, DEFAULT_SWEEP_MS, defaultReadPgid };
