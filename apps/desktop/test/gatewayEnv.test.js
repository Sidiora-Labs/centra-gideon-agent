const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { INSTALL_KIND, buildGatewayEnv } = require("../src/gateway/environment");

describe("buildGatewayEnv", () => {
  const base = {
    PATH: "/usr/bin:/bin",
    HOME: "/Users/someone",
    GIDEON_PORT: "8765",
  };
  const built = () =>
    buildGatewayEnv({ env: base, loginPath: "/opt/homebrew/bin:/usr/bin", projectDir: "/app/resources" });

  it("declares the install kind, so the gateway never has to guess", () => {
    assert.strictEqual(built().GIDEON_INSTALL_KIND, "desktop");
    assert.strictEqual(INSTALL_KIND, "desktop");
  });

  it("overrides an INHERITED install kind rather than deferring to it", () => {
    const env = buildGatewayEnv({
      env: { ...base, GIDEON_INSTALL_KIND: "container" },
      loginPath: "/usr/bin",
      projectDir: "/app/resources",
    });
    assert.strictEqual(env.GIDEON_INSTALL_KIND, "desktop");
  });

  it("drops an inherited GIDEON_PORT so `--port auto` is honored", () => {
    assert.ok(!("GIDEON_PORT" in built()), "GIDEON_PORT must not reach the child");
  });

  it("replaces PATH with the resolved login-shell PATH and keeps the rest of the env", () => {
    const env = built();
    assert.strictEqual(env.PATH, "/opt/homebrew/bin:/usr/bin");
    assert.strictEqual(env.HOME, "/Users/someone");
  });

  it("requires local token authentication and the supplied project directory", () => {
    const env = built();
    assert.strictEqual(env.GIDEON_AUTH_MODE, "local_token");
    assert.strictEqual(env.GIDEON_BIND_HOST, "127.0.0.1");
    assert.ok(!("GIDEON_DEV_NO_AUTH" in env));
    assert.strictEqual(env.GIDEON_PROJECT_DIR, "/app/resources");
  });

  it("never mutates the environment it was handed", () => {
    const snapshot = { ...base };
    built();
    assert.deepStrictEqual(base, snapshot);
  });

  it("has no Electron import, so it is testable without a display", () => {
    const src = require("node:fs").readFileSync(require.resolve("../src/gateway/environment.js"), "utf8");
    assert.ok(!/require\("electron"\)/.test(src), "gatewayEnv.js must stay a pure module");
  });
});


it("strips inherited desktop bypasses and public bind overrides", () => {
  const env = buildGatewayEnv({ env: { GIDEON_AUTH_MODE: "none", GIDEON_DEV_NO_AUTH: "1",
    GIDEON_BYPASS_LOCAL_NETWORKS: "1", GIDEON_BIND_HOST: "0.0.0.0" } });
  assert.equal(env.GIDEON_AUTH_MODE, "local_token");
  assert.equal(env.GIDEON_BIND_HOST, "127.0.0.1");
  assert.equal(env.GIDEON_DEV_NO_AUTH, undefined);
  assert.equal(env.GIDEON_BYPASS_LOCAL_NETWORKS, undefined);
});
