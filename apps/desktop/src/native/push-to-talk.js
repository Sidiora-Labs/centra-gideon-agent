"use strict";

const DEFAULT_CHORD = "CommandOrControl+Shift+Space";
const MAX_CAPTURE_MS = 120000;
const MODIFIERS = new Set(["Command", "Cmd", "Control", "Ctrl", "CommandOrControl", "CmdOrCtrl",
  "Alt", "Option", "AltGr", "Shift", "Super", "Meta"]);
const KEYS = new Set([..."ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", ...Array.from({ length: 24 }, (_, index) => `F${index + 1}`),
  "Space", "Tab", "Backspace", "Delete", "Insert", "Return", "Enter", "Up", "Down", "Left", "Right", "Home", "End",
  "PageUp", "PageDown", "Escape", "Esc", "Plus", "-", "=", "[", "]", "\\", ";", "'", ",", ".", "/", "`"]);

function validateChord(chord) {
  const refusal = (reason) => ({ ok: false, reason });
  if (typeof chord !== "string" || !chord.trim()) return refusal("Enter a shortcut.");
  const components = chord.split("+").map((part) => part.trim());
  if (components.includes("")) return refusal(`“${chord}” has an empty key — check the “+” separators.`);
  const terminal = components.pop();
  if (MODIFIERS.has(terminal)) return refusal("A shortcut needs a key after its modifiers.");
  if (!KEYS.has(terminal)) return refusal(`“${terminal}” is not a key a global shortcut can use.`);
  const invalid = components.find((modifier) => !MODIFIERS.has(modifier));
  if (invalid !== undefined) return refusal(`“${invalid}” is not a modifier key.`);
  if (!components.length) return refusal("Add a modifier (⌘, ⌃, ⌥ or ⇧) — a single key would be captured from every app.");
  if (new Set(components).size !== components.length) return refusal("A modifier is repeated.");
  return { ok: true, reason: "" };
}

class CaptureShortcut {
  constructor(dependencies) {
    this.platform = dependencies.globalShortcut || null;
    this.emit = typeof dependencies.send === "function" ? dependencies.send : () => {};
    this.changed = typeof dependencies.onCapturing === "function" ? dependencies.onCapturing : () => {};
    this.schedule = dependencies.setTimer || setTimeout;
    this.cancel = dependencies.clearTimer || clearTimeout;
    this.ceiling = typeof dependencies.maxCaptureMs === "number" ? dependencies.maxCaptureMs : MAX_CAPTURE_MS;
    this.state = { chord: "", capturing: false, deadline: null };
  }

  release() {
    const previous = this.state.chord;
    this.state.chord = "";
    if (!previous) return;
    try { this.platform?.unregister?.(previous); } catch {}
  }

  bind(chord) {
    const result = (ok, reason = "", conflict = false) => ({ ok, chord: ok ? chord : "", conflict, reason });
    const validation = validateChord(chord);
    if (!validation.ok) return result(false, validation.reason);
    if (!this.platform) return result(false, "Global shortcuts are unavailable in this build.");
    this.release();
    try {
      if (this.platform.isRegistered?.(chord)) return result(false,
        `${chord} is already used by another app — pick a different shortcut.`, true);
      if (!this.platform.register(chord, () => this.press())) return result(false,
        `The system would not give ${chord} to Gideon — pick a different shortcut.`, true);
    } catch (error) { return result(false, `Could not bind ${chord}: ${error?.message || "unknown error"}`); }
    this.state.chord = chord;
    return result(true);
  }

  press() { this.emit({ action: "toggle" }); }

  clearDeadline() {
    if (this.state.deadline !== null) this.cancel(this.state.deadline);
    this.state.deadline = null;
  }

  report(value) {
    const capturing = Boolean(value);
    if (capturing === this.state.capturing) return capturing;
    this.clearDeadline();
    this.state.capturing = capturing;
    this.changed(capturing);
    if (capturing) this.state.deadline = this.schedule(() => {
      this.state.deadline = null;
      this.emit({ action: "stop", reason: "capture-timeout" });
    }, this.ceiling);
    return this.state.capturing;
  }

  disconnect() {
    this.clearDeadline();
    if (this.state.capturing) {
      this.state.capturing = false;
      this.changed(false);
    }
  }
}

function makePushToTalk(dependencies = {}) {
  const controller = new CaptureShortcut(dependencies);
  return { DEFAULT_CHORD, validateChord, bind: (chord) => controller.bind(chord), unbind: () => controller.release(),
    press: () => controller.press(), setCapturing: (value) => controller.report(value), clearCapturing: () => controller.disconnect(),
    boundChord: () => controller.state.chord, isCapturing: () => controller.state.capturing };
}

function registerPushToTalkIpc(ipcMain, controller, channels) {
  const operations = [[channels.hotkeyBind, (value) => controller.bind(value)],
    [channels.capturing, (value) => controller.setCapturing(value)]];
  for (const [channel, handle] of operations) ipcMain.handle(channel, (_event, value) => handle(value));
}

module.exports = { DEFAULT_CHORD, MAX_CAPTURE_MS, MODIFIERS, KEYS, validateChord, makePushToTalk, registerPushToTalkIpc };
