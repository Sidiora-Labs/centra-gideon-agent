"use strict";
const fs = require("node:fs");
const { defaultReadPgid } = require("./shutdown");
const pause = (duration) => new Promise((resolve) => setTimeout(resolve, duration));

function liveGroup(group) {
  if (process.platform === "linux") {
    for (const pid of fs.readdirSync("/proc").filter((name) => /^\d+$/.test(name))) {
      try {
        const fields = fs.readFileSync(`/proc/${pid}/stat`, "utf8").split(") ").pop().split(" ");
        if (Number(fields[2]) === group && fields[0] !== "Z") return true;
      } catch {}
    }
    return false;
  }
  try { process.kill(-group, 0); return true; } catch { return false; }
}

function rememberGroup(child) {
  return Number.isInteger(child.pid) && child.pid > 1 && defaultReadPgid(child.pid) === child.pid ? child.pid : null;
}

async function retireLostGroup(group) {
  if (!Number.isInteger(group) || group <= 1) return { stopped: false };
  if (!liveGroup(group)) return { stopped: true };
  for (const signal of ["SIGTERM", "SIGKILL"]) {
    try { process.kill(-group, signal); } catch (error) { if (error.code !== "ESRCH") return { stopped: false }; }
    for (let attempt = 0; attempt < 10; attempt++) {
      if (!liveGroup(group)) return { stopped: true };
      await pause(50);
    }
  }
  return { stopped: false };
}

function exitDetail({ code, signal, stderr = "" }) {
  return `The local gateway stopped (${signal ? `signal ${signal}` : `exit ${code ?? "unknown"}`}).${stderr ? `\n\n${stderr.slice(-4000)}` : ""}`;
}

module.exports = { rememberGroup, retireLostGroup, exitDetail, liveGroup };
