"use strict";

const INSTALL_KIND = "desktop";

function buildGatewayEnv({ env = {}, loginPath = "", projectDir = "" } = {}) {
  const child = Object.fromEntries(Object.entries(env).filter(([key]) => !["GIDEON_PORT", "GIDEON_DEV_NO_AUTH", "GIDEON_AUTH_MODE", "GIDEON_BYPASS_LOCAL_NETWORKS", "GIDEON_BIND_HOST"].includes(key)));
  Object.assign(child, { PATH: loginPath, GIDEON_AUTH_MODE: "local_token", GIDEON_BIND_HOST: "127.0.0.1",
    GIDEON_PROJECT_DIR: projectDir, GIDEON_INSTALL_KIND: INSTALL_KIND });
  return child;
}

module.exports = { INSTALL_KIND, buildGatewayEnv };
