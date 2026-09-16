"use strict";

const INSTALL_KIND = "desktop";

function buildGatewayEnv({ env = {}, loginPath = "", projectDir = "" } = {}) {
  const child = Object.fromEntries(Object.entries(env).filter(([key]) => key !== "GIDEON_PORT"));
  Object.assign(child, { PATH: loginPath, GIDEON_DEV_NO_AUTH: "1",
    GIDEON_PROJECT_DIR: projectDir, GIDEON_INSTALL_KIND: INSTALL_KIND });
  return child;
}

module.exports = { INSTALL_KIND, buildGatewayEnv };
