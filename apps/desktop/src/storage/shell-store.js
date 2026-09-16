"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const STORE_ABSENT = "absent", STORE_EMPTY = "empty", STORE_UNPARSEABLE = "unparseable";
const STORE_UNREADABLE = "unreadable", STORE_OK = "ok";
const STORE_STATUSES = Object.freeze([STORE_ABSENT, STORE_EMPTY, STORE_UNPARSEABLE, STORE_UNREADABLE, STORE_OK]);
const STORE_DIR_NAME = "desktop", STORE_FILE_NAME = "shell-store.json";
const storePath = (home) => path.join(home, STORE_DIR_NAME, STORE_FILE_NAME);

function readStoreFile(file, { fsMod = fs } = {}) {
  let content;
  try { content = fsMod.readFileSync(file, "utf8"); }
  catch (error) {
    return error?.code === "ENOENT"
      ? { status: STORE_ABSENT, data: {}, reason: "no_file" }
      : { status: STORE_UNREADABLE, data: {}, reason: String(error?.code || "read_failed") };
  }
  if (!content?.trim()) return { status: STORE_EMPTY, data: {}, reason: "zero_length" };
  let decoded;
  try { decoded = JSON.parse(content); }
  catch (error) { return { status: STORE_UNPARSEABLE, data: {}, reason: "invalid_json", detail: String(error?.message || "") }; }
  if (!decoded || Array.isArray(decoded) || typeof decoded !== "object") {
    return { status: STORE_UNPARSEABLE, data: {}, reason: "not_an_object" };
  }
  const pairs = Object.entries(decoded);
  const invalid = pairs.filter(([, value]) => typeof value !== "string").map(([key]) => key);
  const data = Object.fromEntries(pairs.filter(([, value]) => typeof value === "string"));
  return invalid.length
    ? { status: STORE_UNPARSEABLE, data, reason: "non_string_values", detail: invalid.join(",") }
    : { status: STORE_OK, data, reason: "" };
}

function inspect(file, { fsMod = fs, uid } = {}) {
  const owner = uid === undefined ? (process.getuid?.() ?? null) : uid;
  let metadata;
  try { metadata = fsMod.statSync(file); }
  catch (error) {
    const absent = error?.code === "ENOENT";
    return { exists: false, ownedByUs: absent, ownerOnly: absent, mode: null,
      dirOwnerOnly: absent, safe: absent, reason: absent ? "absent" : "stat_failed" };
  }
  const mode = metadata.mode & 0o777;
  const ownedByUs = owner === null || owner === metadata.uid;
  const ownerOnly = (mode & 0o077) === 0;
  let dirOwnerOnly = false, directoryReason = "directory_unreadable";
  try {
    const directory = fsMod.statSync(path.dirname(file));
    dirOwnerOnly = (directory.mode & 0o022) === 0 && (owner === null || owner === directory.uid);
    directoryReason = "directory_writable_by_others";
  } catch {}
  const failure = [
    [!ownedByUs, "owned_by_another_user"], [!ownerOnly, "readable_or_writable_by_others"],
    [!dirOwnerOnly, directoryReason], [owner === null, "ownership_not_checkable_on_this_platform"],
  ].find(([applies]) => applies);
  return { exists: true, ownedByUs, ownerOnly, mode, dirOwnerOnly,
    safe: ownedByUs && ownerOnly && dirOwnerOnly, reason: failure?.[1] || "" };
}

function writeStoreFile(file, data, { fsMod = fs } = {}) {
  const directory = path.dirname(file);
  fsMod.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const temporary = path.join(directory, `.${STORE_FILE_NAME}.${process.pid}.${Date.now()}.tmp`);
  fsMod.writeFileSync(temporary, JSON.stringify(data, null, 2), { mode: 0o600 });
  try { fsMod.renameSync(temporary, file); }
  catch (error) {
    try { fsMod.unlinkSync(temporary); } catch {}
    throw error;
  }
  try { fsMod.chmodSync(file, 0o600); } catch {}
}

function openShellStore({ home, fsMod = fs, uid, log } = {}) {
  const file = storePath(home || process.env.GIDEON_HOME || path.join(os.homedir(), ".gideon"));
  const loaded = readStoreFile(file, { fsMod });
  const entries = new Map(Object.entries(loaded.data));
  let readOnly = false;
  const persist = () => {
    if (readOnly) return;
    try { writeStoreFile(file, Object.fromEntries(entries), { fsMod }); }
    catch (error) {
      readOnly = true;
      log?.(`shell store is not writable (${error?.message}); changes are this-session only`);
    }
  };
  return {
    file, status: loaded.status, storeReason: loaded.reason || "", storeDetail: loaded.detail || "",
    permissions: inspect(file, { fsMod, uid }),
    get readOnly() { return readOnly; },
    get length() { return entries.size; },
    getItem(key) { return entries.get(String(key)) ?? null; },
    setItem(key, value) { entries.set(String(key), String(value)); persist(); },
    removeItem(key) { entries.delete(String(key)); persist(); },
    key(index) { return Array.from(entries.keys())[index] ?? null; },
  };
}

module.exports = { STORE_ABSENT, STORE_EMPTY, STORE_UNPARSEABLE, STORE_UNREADABLE, STORE_OK, STORE_STATUSES,
  STORE_DIR_NAME, STORE_FILE_NAME, storePath, readStoreFile, writeStoreFile, inspect, openShellStore };
