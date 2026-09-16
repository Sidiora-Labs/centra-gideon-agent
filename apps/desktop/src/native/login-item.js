"use strict";

const SUPPORTED_PLATFORMS = ["darwin", "win32"];

function makeLoginItem({ app, platform = process.platform, log = () => {} } = {}) {
  const supported = SUPPORTED_PLATFORMS.includes(platform) && Boolean(app);
  const unavailable = `login items are not implemented on ${platform}`;
  const isEnabled = () => {
    if (supported) {
      try { return Boolean(app.getLoginItemSettings()?.openAtLogin); }
      catch (error) { log(`login item state unreadable: ${error.message}`); }
    }
    return false;
  };
  return {
    supported, isEnabled,
    describe() {
      if (!supported) return unavailable;
      return platform === "darwin"
        ? "macOS Login Items (System Settings → General → Login Items) for this app bundle only"
        : "the current user's Run registry key for this app only";
    },
    set(enabled) {
      const before = isEnabled(), requested = Boolean(enabled);
      const result = { ok: false, enabled: before, changed: false, supported };
      if (!supported) return { ...result, reason: unavailable };
      if (before === requested) return { ...result, ok: true };
      try { app.setLoginItemSettings({ openAtLogin: requested, openAsHidden: true }); }
      catch (error) {
        log(`login item write failed: ${error.message}`);
        return { ...result, reason: error.message };
      }
      const after = isEnabled();
      return { ok: after === requested, enabled: after, changed: after !== before, supported,
        ...(after !== requested ? { reason: "the OS did not apply the change" } : {}) };
    },
  };
}

function registerLoginItemIpc(ipcMain, loginItem, channels, onChanged = () => {}) {
  const read = () => ({ enabled: loginItem.isEnabled(), supported: loginItem.supported, describes: loginItem.describe() });
  const write = (_event, requested) => {
    const result = loginItem.set(Boolean(requested));
    onChanged(result);
    return result;
  };
  ipcMain.handle(channels.loginItemGet, read);
  ipcMain.handle(channels.loginItemSet, write);
}

module.exports = { makeLoginItem, registerLoginItemIpc, SUPPORTED_PLATFORMS };
