"use strict";

function createLoginPathResolver({ env, execFileSync, warn }) {
  const fallback = env.PATH || "/usr/bin:/bin:/usr/sbin:/sbin";
  let resolved;
  return () => {
    if (resolved !== undefined) return resolved;
    resolved = fallback;
    if (fallback.includes("/.nvm/") || fallback.includes("/homebrew/") || fallback.split(":").length > 6) return resolved;
    try {
      const output = execFileSync(env.SHELL || "/bin/zsh",
        ["-ilc", "printf '__GIDEON_PATH__%s__GIDEON_PATH__' \"$PATH\""],
        { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"] });
      const start = output.indexOf("__GIDEON_PATH__");
      const end = output.indexOf("__GIDEON_PATH__", start + 15);
      const candidate = start < 0 || end < 0 ? "" : output.slice(start + 15, end).trim();
      if (candidate.includes("/")) resolved = candidate;
    } catch (error) { warn("login-shell PATH resolve failed:", error.message); }
    return resolved;
  };
}

module.exports = { createLoginPathResolver };
