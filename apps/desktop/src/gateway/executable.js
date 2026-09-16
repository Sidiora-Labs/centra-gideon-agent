"use strict";

function findGideonBin(fs, os, path, resourcesPath, dirname) {
  const bundled = [resourcesPath || "", dirname].map((root, index) =>
    (index ? path.resolve : path.join)(root, "backend-dist", "gideon-backend", "gideon-backend"));
  const candidates = bundled.concat(path.resolve(dirname, "..", "..", "bin", "gideon"),
    path.join(os.homedir(), ".local", "bin", "gideon"),
    path.join(os.homedir(), ".gideon-app", ".venv", "bin", "gideon"));
  const usable = candidates.find((candidate) => {
    try { fs.accessSync(candidate, fs.constants.X_OK); return true; }
    catch (error) {
      if (error.code !== "ENOENT") console.warn(`gideon candidate ${candidate}: ${error.code}`);
      return false;
    }
  });
  return usable || "gideon";
}

module.exports = { findGideonBin };
