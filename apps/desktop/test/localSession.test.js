"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { parseReadyLine, movedUrl, localRequestHeaders } = require("../src/gateway/local-session");

test("ready IPC names only the owned loopback origin", () => {
  assert.deepEqual(parseReadyLine('GIDEON_READY:{"port":12345,"token":"local","url":"https://remote.invalid"}'),
    { port: 12345, token: "local", url: "http://localhost:12345" });
  for (const payload of ['{"port":0}', '{"port":65536}', '{"port":"12345"}', 'broken']) {
    assert.equal(parseReadyLine(`GIDEON_READY:${payload}`), null);
  }
});

test("restart relocation preserves native page path and hash without token queries", () => {
  assert.equal(movedUrl("http://localhost:12345/tasks?token=secret#/settings", "http://localhost:12345", "http://localhost:12346"),
    "http://localhost:12346/tasks#/settings");
  assert.equal(movedUrl("https://paired.invalid/tasks", "http://localhost:12345", "http://localhost:12346"), null);
});

test("local browser credentials cannot follow remote or another loopback port requests", () => {
  const headers = { Cookie: "gideon_token_12345=secret", authorization: "Bearer secret", Accept: "text/html" };
  assert.deepEqual(localRequestHeaders("http://localhost:12345/api/status", "http://localhost:12345", headers), headers);
  for (const target of ["https://remote.invalid", "http://localhost:12346", "http://127.0.0.1:12345", "invalid"]) {
    assert.deepEqual(localRequestHeaders(target, "http://localhost:12345", headers), { Accept: "text/html" });
  }
  assert.equal(headers.Cookie, "gideon_token_12345=secret");
});
