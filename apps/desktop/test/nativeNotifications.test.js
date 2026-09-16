const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  makeNativeNotifications,
  registerNativeNotificationIpc,
  normalizeRoute,
  MAX_TITLE,
  MAX_BODY,
} = require("../src/native/notifications");
const { IPC_CHANNELS } = require("../src/native/capabilities");


function fakeNotification({ supported = true, throwOn = null } = {}) {
  const raised = [];
  class N {
    constructor(opts) {
      if (throwOn === "construct") throw new Error("boom");
      this.opts = opts;
      this.handlers = {};
      this.shown = 0;
      raised.push(this);
    }
    on(event, cb) {
      if (throwOn === "on") throw new Error("no listeners");
      this.handlers[event] = cb;
      return this;
    }
    show() {
      if (throwOn === "show") throw new Error("display failed");
      this.shown += 1;
    }
    tap() {
      this.handlers.click?.();
    }
  }
  N.isSupported = () => supported;
  N.raised = raised;
  return N;
}

function make(opts = {}) {
  const Notification = opts.Notification ?? fakeNotification(opts);
  const focused = [];
  const sent = [];
  const logs = [];
  const native = makeNativeNotifications({
    Notification,
    focusWindow: () => {
      focused.push(true);
      return opts.hasWindow !== false;
    },
    sendToRenderer: (p) => sent.push(p),
    log: (m) => logs.push(m),
  });
  return { native, Notification, focused, sent, logs };
}

describe("makeNativeNotifications", () => {
  it("raises nothing on construction", () => {
    const { Notification } = make();
    assert.deepStrictEqual(Notification.raised, []);
  });

  it("constructs and shows one notification for one show() call", () => {
    const { native, Notification } = make();
    const res = native.show({ title: "Loop stalled", body: "needs an answer", route: "loops" });
    assert.deepStrictEqual(res, { ok: true, route: "loops" });
    assert.strictEqual(Notification.raised.length, 1);
    assert.strictEqual(Notification.raised[0].shown, 1);
    assert.strictEqual(Notification.raised[0].opts.title, "Loop stalled");
    assert.strictEqual(Notification.raised[0].opts.body, "needs an answer");
  });

  it("focuses the window and forwards the route when tapped", () => {
    const { native, Notification, focused, sent } = make();
    native.show({ title: "Approval waiting", body: "", route: "inbox" });
    Notification.raised[0].tap();
    assert.deepStrictEqual(focused, [true]);
    assert.deepStrictEqual(sent, [{ route: "inbox" }]);
  });

  it("still focuses when the note named no surface", () => {
    const { native, Notification, focused, sent } = make();
    native.show({ title: "Backup finished", body: "", route: "" });
    Notification.raised[0].tap();
    assert.deepStrictEqual(focused, [true]);
    assert.deepStrictEqual(sent, [], "nothing to navigate to");
  });

  it("logs rather than throws when there is no window to focus", () => {
    const { native, Notification, logs } = make({ hasWindow: false });
    native.show({ title: "t", route: "inbox" });
    Notification.raised[0].tap();
    assert.match(logs.join("\n"), /no window to focus/);
  });

  it("refuses a titleless notification instead of showing a blank banner", () => {
    const { native, Notification } = make();
    const res = native.show({ body: "orphaned body", route: "inbox" });
    assert.strictEqual(res.ok, false);
    assert.match(res.reason, /needs a title/);
    assert.deepStrictEqual(Notification.raised, []);
  });

  it("reports unsupported instead of throwing", () => {
    const { native } = make({ supported: false });
    assert.strictEqual(native.supported, false);
    const res = native.show({ title: "t" });
    assert.strictEqual(res.ok, false);
    assert.match(res.reason, /does not support notifications/);
  });

  it("survives a throwing constructor and a throwing show()", () => {
    for (const throwOn of ["construct", "show"]) {
      const { native } = make({ Notification: fakeNotification({ throwOn }) });
      const res = native.show({ title: "t", body: "b" });
      assert.strictEqual(res.ok, false, throwOn);
      assert.ok(res.reason, throwOn);
    }
  });

  it("truncates title and body in the process that owns the boundary", () => {
    const { native, Notification } = make();
    native.show({ title: "T".repeat(500), body: "B".repeat(5000) });
    assert.strictEqual(Notification.raised[0].opts.title.length, MAX_TITLE);
    assert.strictEqual(Notification.raised[0].opts.body.length, MAX_BODY);
  });

  it("coerces a non-object argument rather than throwing", () => {
    const { native } = make();
    for (const junk of [null, undefined, 7, "hi", []]) {
      assert.strictEqual(native.show(junk).ok, false);
    }
  });
});

describe("normalizeRoute", () => {
  it("accepts SPA route ids and strips a leading hash", () => {
    assert.strictEqual(normalizeRoute("inbox"), "inbox");
    assert.strictEqual(normalizeRoute("#/loops"), "loops");
    assert.strictEqual(normalizeRoute("#loops"), "loops");
    assert.strictEqual(normalizeRoute("mission-control"), "mission-control");
  });

  it("rejects rather than repairs anything that is not a route id", () => {
    for (const bad of [
      "https://evil.example/x",
      "javascript:alert(1)",
      "../../etc/passwd",
      "loops?x=1",
      "Loops",
      "",
      null,
      undefined,
      {},
    ]) {
      assert.strictEqual(normalizeRoute(bad), "", String(bad));
    }
  });
});

describe("registerNativeNotificationIpc", () => {
  it("registers exactly the notify channel and nothing else", () => {
    const handlers = new Map();
    const ipc = { handle: (ch, fn) => handlers.set(ch, fn) };
    const { native } = make();
    registerNativeNotificationIpc(ipc, native, IPC_CHANNELS);
    assert.deepStrictEqual([...handlers.keys()], [IPC_CHANNELS.notify]);
  });

  it("routes an invoke through to show()", () => {
    const handlers = new Map();
    const ipc = { handle: (ch, fn) => handlers.set(ch, fn) };
    const { native, Notification } = make();
    registerNativeNotificationIpc(ipc, native, IPC_CHANNELS);
    const res = handlers.get(IPC_CHANNELS.notify)(null, { title: "t", route: "inbox" });
    assert.deepStrictEqual(res, { ok: true, route: "inbox" });
    assert.strictEqual(Notification.raised.length, 1);
  });
});

describe("main.js wiring", () => {
  const main = fs.readFileSync(path.join(__dirname, "..", "src/application/desktop-application.js"), "utf8");

  it("registers the notify handler in the main process", () => {
    assert.match(main, /registerNativeNotificationIpc\(ipcMain, this\.notifications, IPC_CHANNELS\)/);
    assert.match(main, /makeNativeNotifications\(\{/);
  });

  it("pushes taps on the activate channel", () => {
    assert.match(main, /IPC_CHANNELS\.notificationActivate/);
  });
});

describe("preload notifications surface", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "src/bridge/dashboard-preload.js"), "utf8");

  it("exposes show() and on() and no token accessor", () => {
    assert.match(src, /notifications:\s*\{/);
    assert.match(src, /ipcRenderer\.invoke\(IPC_CHANNELS\.notify/);
    assert.match(src, /subscribe\(IPC_CHANNELS\.notificationActivate/);
    assert.match(src, /ipcRenderer\.on\(channel, listener\)/);
    assert.match(src, /ipcRenderer\.removeListener\(channel, listener\)/);
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    assert.ok(code.includes("notifications:"), "the scan must still see the code");
    assert.ok(!/token/i.test(code), "the bridge must not reach the token");
  });
});
