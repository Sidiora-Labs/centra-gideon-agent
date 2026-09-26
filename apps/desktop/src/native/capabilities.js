"use strict";

const CAPABILITIES = ["audio_capture", "global_hotkey", "native_notifications", "tray", "screen_capture", "login_item", "system_audio"];
const GRANT_STATES = ["granted", "denied", "restricted", "not-determined", "unavailable"];
const IPC_PREFIX = "gideon-desktop:";
const IPC_CHANNELS = Object.fromEntries(Object.entries({ probe: "probe", request: "request", snapshot: "snapshot", state: "state",
  hotkeyBind: "hotkey-bind", capturing: "capturing", pushToTalk: "push-to-talk", loginItemGet: "login-item-get",
  loginItemSet: "login-item-set", notify: "notify", notificationActivate: "notification-activate" })
  .map(([key, name]) => [key, IPC_PREFIX + name]));

const SPECS = {
  audio_capture: {
    kind: "tcc",
    media: "microphone",
    platforms: ["darwin"],
    requestable: true,
    label: "Microphone",
  },
  screen_capture: {
    kind: "tcc",
    media: "screen",
    platforms: ["darwin"],
    requestable: false,
    disclosure: "Grant Screen Recording in System Settings › Privacy & Security.",
    label: "Screen recording",
  },
  native_notifications: {
    kind: "opaque",
    platforms: ["darwin", "win32"],
    requestable: false,
    disclosure:
      "macOS does not report notification authorization to the app; the first " +
      "notification asks, and Notifications in System Settings is the only control.",
    label: "Native notifications",
  },
  global_hotkey: { kind: "shell", platforms: ["darwin", "win32"], requestable: false, label: "Global hotkey" },
  system_audio: {
    kind: "unsupported",
    platforms: [],
    requestable: false,
    unsupported:
      "Capturing system audio needs the macOS Screen Recording entitlement (there " +
      "is no audio-only tap), so Gideon captures the microphone only.",
    label: "System audio",
  },
  tray: { kind: "shell", platforms: ["darwin", "win32"], requestable: false, label: "Tray icon" },
  login_item: { kind: "shell", platforms: ["darwin", "win32"], requestable: false, label: "Open at login" },
};


const unavailable = (reason) => ({ available: false, granted: "unavailable", requestable: false, reason });
const answer = (state, reason = "", prompted = false) => ({ granted: state === "granted", state, prompted, reason });

function makeCapabilities({ platform = "", systemPreferences, notification, shellAvailability = {}, onChange } = {}) {
  const nativeReaders = {
    shell: (specification, capability) => {
      const reported = shellAvailability[capability];
      if (platform === "win32" && reported !== undefined && !(typeof reported === "function" ? reported() : reported)) {
        return unavailable(`${specification.label} is unavailable in this session`);
      }
      return { available: true, granted: "granted", requestable: false, reason: "" };
    },
    opaque(specification) {
      if (platform === "win32" && typeof notification?.isSupported !== "function") return unavailable("notification support cannot be checked");
      if (typeof notification?.isSupported === "function" && !notification.isSupported()) return unavailable("the OS does not support notifications");
      return { available: true, granted: "not-determined", requestable: false,
        reason: platform === "win32" ? "Windows notification permission is managed in system settings." : specification.disclosure || "" };
    },
    tcc(specification) {
      if (typeof systemPreferences?.getMediaAccessStatus !== "function") return unavailable("permission state unavailable in this build");
      const grant = systemPreferences.getMediaAccessStatus(specification.media);
      if (!GRANT_STATES.includes(grant) || grant === "unavailable") return unavailable("the OS did not report a state");
      return { available: true, granted: grant, requestable: specification.requestable && grant === "not-determined",
        reason: specification.requestable ? "" : specification.disclosure || "" };
    },
  };
  function probe(capability) {
    const specification = SPECS[capability];
    if (!specification || !CAPABILITIES.includes(capability)) return unavailable("unknown capability");
    if (specification.kind === "unsupported") return unavailable(specification.unsupported);
    if (!specification.platforms.includes(platform)) return unavailable(`not implemented on ${platform || "this platform"}`);
    try { return nativeReaders[specification.kind](specification, capability); }
    catch (error) { return unavailable(`probe failed: ${error?.message || "unknown error"}`); }
  }
  async function request(capability) {
    const current = probe(capability), specification = SPECS[capability];
    if (!current.available) return answer("unavailable", current.reason || "unavailable");
    if (current.granted === "granted") return answer("granted");
    if (!specification.requestable) return answer(current.granted, specification.disclosure || "this capability cannot be requested from the app");
    if (["denied", "restricted"].includes(current.granted)) return answer(current.granted,
      `${specification.label} was already ${current.granted} for Gideon. Change it in System Settings › Privacy & Security.`);
    let permission;
    try { permission = await systemPreferences.askForMediaAccess(specification.media); }
    catch (error) { return answer("not-determined", `request failed: ${error?.message || "unknown error"}`, true); }
    if (typeof onChange === "function") onChange(capability, probe(capability));
    return answer(permission ? "granted" : "denied", "", true);
  }
  return { CAPABILITIES, IPC_CHANNELS, probe, request,
    snapshot: () => Object.fromEntries(CAPABILITIES.map((capability) => [capability, probe(capability)])) };
}

function registerCapabilityIpc(ipcMain, capabilities) {
  const operations = {
    probe: (capability) => CAPABILITIES.includes(capability) ? capabilities.probe(capability) : unavailable("unknown capability"),
    snapshot: () => capabilities.snapshot(),
    request: (capability) => CAPABILITIES.includes(capability) ? capabilities.request(capability) : answer("unavailable", "unknown capability"),
  };
  for (const [name, operation] of Object.entries(operations)) {
    ipcMain.handle(IPC_CHANNELS[name], (_event, capability) => operation(capability));
  }
}

module.exports = { CAPABILITIES, GRANT_STATES, IPC_CHANNELS, IPC_PREFIX, SPECS, makeCapabilities, registerCapabilityIpc };
