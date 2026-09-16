"use strict";

const MAX_TITLE = 120, MAX_BODY = 400;
const clipped = (value, limit) => String(value ?? "").trim().slice(0, limit);

function normalizeRoute(raw) {
  const candidate = clipped(raw, 64).replace(/^#\/?/, "");
  return /^[a-z0-9][a-z0-9/-]*$/.test(candidate) ? candidate : "";
}

function makeNativeNotifications({ Notification, focusWindow = () => false, sendToRenderer = () => {}, log = () => {} } = {}) {
  const supported = Boolean(Notification) && (typeof Notification.isSupported !== "function" || Boolean(Notification.isSupported()));
  return {
    supported,
    show(note) {
      const data = note && typeof note === "object" ? note : {};
      const route = normalizeRoute(data.route), title = clipped(data.title, MAX_TITLE);
      const refusal = !supported ? "the OS does not support notifications" : !title ? "a notification needs a title" : "";
      if (refusal) return { ok: false, route, reason: refusal };
      try {
        const notification = new Notification({ title, body: clipped(data.body, MAX_BODY), silent: false });
        notification.on("click", () => {
          if (!focusWindow()) log("native notification tapped with no window to focus");
          if (route) sendToRenderer({ route });
        });
        notification.show();
        return { ok: true, route };
      } catch (error) {
        log(`native notification failed: ${error.message}`);
        return { ok: false, route, reason: error.message };
      }
    },
  };
}

function registerNativeNotificationIpc(ipcMain, native, channels) {
  ipcMain.handle(channels.notify, (_event, note) => native.show(note));
}

module.exports = { makeNativeNotifications, registerNativeNotificationIpc, normalizeRoute, MAX_TITLE, MAX_BODY };
