/**
 * Locate the gideon backend binary by checking well-known paths in order.
 *
 * Returns the first executable candidate, or bare `"gideon"` as a PATH
 * fallback. Dependencies are injected so the function is pure and testable
 * without mocking globals.
 *
 * @param {typeof import("fs")} fs - Node fs module (needs `accessSync`, `constants.X_OK`)
 * @param {typeof import("os")} os - Node os module (needs `homedir()`)
 * @param {typeof import("path")} path - Node path module
 * @param {string|undefined} resourcesPath - `process.resourcesPath` (Electron only)
 * @param {string} dirname - `__dirname` of the calling module
 * @returns {string} Absolute path to the binary, or `"gideon"`
 */
function findPersonalclawBin(fs, os, path, resourcesPath, dirname) {
  const home = os.homedir();
  const candidates = [
    // 1. Bundled PyInstaller binary (inside .app or dev electron/backend-dist)
    path.join(resourcesPath || "", "backend-dist", "gideon-backend", "gideon-backend"),
    path.resolve(dirname, "backend-dist", "gideon-backend", "gideon-backend"),
    path.resolve(dirname, "..", "bin", "gideon"),
    // 2. Well-known install paths (pip install, venv, homebrew)
    path.join(home, ".local", "bin", "gideon"),
    path.join(home, ".gideon-app", ".venv", "bin", "gideon"),
  ];
  for (const bin of candidates) {
    try {
      fs.accessSync(bin, fs.constants.X_OK);
      return bin;
    } catch (e) {
      if (e.code !== "ENOENT") console.warn(`gideon candidate ${bin}: ${e.code}`);
    }
  }
  return "gideon"; // fall back to PATH
}

module.exports = { findPersonalclawBin };
