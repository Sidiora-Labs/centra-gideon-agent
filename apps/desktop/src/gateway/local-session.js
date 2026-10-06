"use strict";

function parseReadyLine(line) {
  if (typeof line !== "string" || !line.startsWith("GIDEON_READY:")) return null;
  try {
    const value = JSON.parse(line.slice("GIDEON_READY:".length));
    if (!Number.isInteger(value.port) || value.port < 1 || value.port > 65535) return null;
    return { port: value.port, token: typeof value.token === "string" ? value.token : "", url: `http://localhost:${value.port}` };
  } catch { return null; }
}

const cookieName = (port) => `gideon_token_${port}`;

function movedUrl(address, previous, next) {
  try {
    const url = new URL(address);
    return previous && url.origin === previous ? `${next}${url.pathname}${url.hash}` : null;
  } catch { return null; }
}

function localRequestHeaders(address, origin, headers) {
  const result = { ...headers };
  let trusted = false;
  try { trusted = new URL(address).origin === origin; } catch {}
  if (!trusted) {
    for (const key of Object.keys(result)) if (["authorization", "cookie"].includes(key.toLowerCase())) delete result[key];
  }
  return result;
}

class LocalSession {
  #held = null;
  constructor(cookies) { this.cookies = cookies; }
  async adopt(ready) {
    if (ready.url !== `http://localhost:${ready.port}` || !Number.isInteger(ready.port) || ready.port < 1 || ready.port > 65535) throw new Error("Untrusted local gateway session origin");
    if (!ready.token) throw new Error("The local gateway did not provide an authenticated session");
    const previous = this.#held;
    await this.cookies.set({ url: ready.url, name: cookieName(ready.port), value: ready.token,
      path: "/", httpOnly: true, secure: false, sameSite: "lax" });
    this.#held = ready;
    if (previous && previous.port !== ready.port) await this.cookies.remove(previous.url, cookieName(previous.port));
    return previous;
  }
  authorization() { return this.#held ? { Authorization: `Bearer ${this.#held.token}` } : {}; }
  async release() {
    const previous = this.#held;
    this.#held = null;
    if (previous) await this.cookies.remove(previous.url, cookieName(previous.port));
    return previous;
  }
}

module.exports = { LocalSession, parseReadyLine, movedUrl, cookieName, localRequestHeaders };
