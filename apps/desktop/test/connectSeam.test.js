"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { LocalGateway, readyOrigin } = require("../src/application/local-gateway");
const { EndpointSession, waitRemote } = require("../src/application/endpoint-session");
const { DesktopApplication, applicationMenu } = require("../src/application/desktop-application");
const { navigationDecision } = require("../src/application/window-workspace");
const source = (file) => fs.readFileSync(path.join(__dirname, "..", file), "utf8");
const MAIN = source("src/application/main.js");
const APP = source("src/application/desktop-application.js");
const LOCAL = source("src/application/local-gateway.js");
const ENDPOINT = source("src/application/endpoint-session.js");
const WORKSPACE = source("src/application/window-workspace.js");
const body = (type, name) => type.prototype[name].toString();

describe("application controller ownership", () => {
  it("launches the explicit application and owns each required subsystem", () => {
    assert.match(MAIN, /new DesktopApplication\(electron\)\.run\(\)/);
    for (const name of ["LocalGateway", "WindowWorkspace", "EndpointSession", "makeCapabilities", "makePushToTalk", "makeTrayPresence", "makeLoginItem", "makeNativeNotifications"]) assert.ok(APP.includes(name), name);
  });
  it("registers every bridge before any content window is mounted", () => {
    const configure = body(DesktopApplication, "configure");
    for (const name of ["registerCapabilityIpc", "registerPushToTalkIpc", "registerLoginItemIpc", "registerNativeNotificationIpc"]) {
      assert.ok(configure.indexOf(name) < configure.indexOf("this.workspace.mount(window)"), name);
    }
  });
  it("keeps single-instance, native activation, tab and orderly shutdown wiring", () => {
    for (const event of ["before-quit", "window-all-closed", "second-instance", "activate", "new-window-for-tab"]) assert.ok(APP.includes(`"${event}"`), event);
    assert.match(body(DesktopApplication, "run"), /requestSingleInstanceLock/);
    const quit = body(DesktopApplication, "beforeQuit");
    assert.match(quit, /if \(this.state.shutdown\) return/);
    for (const action of ["this.capture.unbind()", "this.capture.clearCapturing()", "this.endpoints.cancelReachability()", "this.gateway.unregister()", "this.gateway.stop()", "this.tray.destroy()", "this.state.complete = true"]) assert.ok(quit.includes(action), action);
  });
  it("retains the complete application menu and accelerators", () => {
    const menu = applicationMenu({});
    assert.deepEqual(menu.map(item => item.role || item.label), ["appMenu", "editMenu", "Tab", "Gateway", "windowMenu"]);
    assert.deepEqual(menu[2].submenu.filter(item => item.accelerator).map(item => item.accelerator), ["CmdOrCtrl+T", "CmdOrCtrl+Shift+R"]);
    assert.equal(menu[3].submenu[0].accelerator, "CmdOrCtrl+Shift+G");
  });
});

describe("bridge isolation and credentials", () => {
  it("attaches exactly one dashboard preload only under the bridge decision", () => {
    const sites = [...WORKSPACE.matchAll(/preload:\s*path\.join\(__dirname,\s*"\.\.\/bridge\/dashboard-preload\.js"\)/g)];
    assert.equal(sites.length, 1);
    assert.match(WORKSPACE.slice(sites[0].index - 30, sites[0].index), /attachBridge/);
    assert.match(body(EndpointSession, "navigate"), /policy.shouldAttachBridge\(address\)/);
    assert.match(WORKSPACE, /attachBridge: shouldAttachBridge\(target\)/);
    assert.doesNotMatch(APP + ENDPOINT + WORKSPACE, /attachBridge:\s*true/);
  });
  it("keeps the two browser bridge surfaces separate", () => {
    const dashboard = source("src/bridge/dashboard-preload.js"), chooser = source("src/bridge/connection-preload.js");
    assert.doesNotMatch(dashboard, /gideonConnect|connectDialog/);
    assert.doesNotMatch(chooser, /gideonDesktop|capabilities/);
  });
  it("validates the owned origin and final request target before any HTTP transmission", () => {
    const request = body(LocalGateway, "request");
    assert.match(request, /assertLoopbackTarget\(this.url/);
    assert.match(request, /assertLoopbackTarget\(target.href/);
    assert.ok(request.indexOf("assertLoopbackTarget(target.href") < request.indexOf("http.request("));
    assert.doesNotMatch(request, /activeUrl|EndpointSession/);
  });
  it("stores the shell token in a private field and sends credentials only through the protected transport", () => {
    assert.match(LOCAL, /#shellToken = null/);
    assert.match(body(LocalGateway, "register"), /this.request\("POST", "\/api\/desktop\/register", manifest, \{ "X-Local-Secret": secret \}/);
    for (const name of ["publish", "unregister"]) assert.match(body(LocalGateway, name), /this.request\("POST"/);
    assert.doesNotMatch(APP + ENDPOINT + WORKSPACE, /shellToken|X-Local-Secret|X-Shell-Token/);
  });
});

describe("gateway launch and readiness contracts", () => {
  it("recognizes valid READY lines and rejects malformed or out-of-range ports", () => {
    assert.equal(readyOrigin('GIDEON_READY:{"port":43210}'), "http://localhost:43210");
    for (const line of ['OTHER:{"port":43210}', 'GIDEON_READY:broken', 'GIDEON_READY:{}', 'GIDEON_READY:{"port":0}', 'GIDEON_READY:{"port":65536}']) assert.equal(readyOrigin(line), null);
  });
  it("preserves ephemeral-port argv and constructs auth-off only for the owned child", () => {
    assert.match(LOCAL, /\["gateway", "--port", "auto", "--json-ready", "--no-open"\]/);
    assert.equal([...LOCAL.matchAll(/buildGatewayEnv\(/g)].length, 1);
    assert.match(body(LocalGateway, "start"), /this.child = spawn/);
    assert.match(source("src/gateway/environment.js"), /GIDEON_DEV_NO_AUTH: "1"/);
    assert.doesNotMatch(APP + ENDPOINT, /GIDEON_DEV_NO_AUTH/);
  });
  it("retains distinct local readiness and terminal remote-refusal handling", () => {
    const local = body(LocalGateway, "waitReady");
    assert.match(local, /"\/api\/status"/);
    assert.match(local, /status < 500/);
    assert.doesNotMatch(local, /probeEndpoint/);
    assert.match(waitRemote.toString(), /policy.probeEndpoint/);
    assert.match(waitRemote.toString(), /action !== "retry"/);
  });
});

describe("endpoint reconnection and confirmation", () => {
  it("has one cancellable reachability job and reloads only after reachable", () => {
    const schedule = body(EndpointSession, "scheduleReachability");
    assert.match(schedule, /setTimeout/);
    assert.doesNotMatch(schedule, /setInterval/);
    assert.match(schedule, /policy.nextReconnectStep/);
    assert.match(schedule, /step.action === "stay"\) \{ window.webContents.loadURL\(address\)/);
    assert.match(schedule, /epoch !== this.state.epoch/);
    assert.equal([...APP.matchAll(/setInterval\(/g)].length, 1);
  });
  it("resolves and forwards a current fingerprint at both connection decisions", () => {
    assert.match(body(EndpointSession, "fingerprint"), /await policy.currentFingerprintFor\(row.base_url\)/);
    for (const name of ["chooseStartup", "select"]) {
      const method = body(EndpointSession, name);
      assert.match(method, /currentFingerprint = await this.fingerprint\(row\)/);
      assert.match(method, /policy\.(describeStartup|switchTo)\([^;]+currentFingerprint/);
    }
  });
  it("replaces content when bridge eligibility changes", () => {
    const method = body(EndpointSession, "navigate");
    assert.match(method, /this.workspace.hasBridge\(window\) !== attachBridge/);
    assert.match(method, /this.workspace.mount\(window, \{ attachBridge \}\)/);
  });
  it("reads gateway discovery through the authenticated page and keeps labels locally scoped", () => {
    const method = body(EndpointSession, "discoverName");
    assert.match(method, /page.executeJavaScript/);
    assert.match(method, /fetch\('\/api\/companion\/discovery', \{ credentials: 'same-origin' \}/);
    assert.match(method, /policy.adoptGatewayLabel\(this.state.store, row.id, name\)/);
  });
  it("preserves loading retry and paired-gateway fallback", () => {
    assert.match(body(EndpointSession, "loadLocal"), /buttons: \["Retry", "Quit"\]/);
    assert.match(body(EndpointSession, "loadLocal"), /page.loadURL\(this.gateway.url\)/);
    assert.match(body(EndpointSession, "navigate"), /this.navigate\(window, this.gateway.url, true\)/);
  });
});

describe("workspace navigation decisions", () => {
  it("keeps confirmed-origin content and sends external HTTPS links to the browser", () => {
    assert.equal(navigationDecision("https://gateway.example/chat", "https://gateway.example").allowed, true);
    assert.deepEqual(navigationDecision("https://other.example/chat", "https://gateway.example"), { allowed: false, external: "https://other.example/chat", origin: "https://other.example" });
    assert.match(WORKSPACE, /on\("will-navigate", guardNavigation\)/);
    assert.match(WORKSPACE, /on\("will-redirect", guardNavigation\)/);
  });
  it("allows local loading documents only in existing view navigation", () => {
    for (const address of ["file:///app/loading.html", "about:blank"]) {
      assert.equal(navigationDecision(address, "", true).allowed, true);
      assert.equal(navigationDecision(address, "", false).allowed, false);
    }
  });
  it("blocks invalid addresses and executable schemes without external launch", () => {
    for (const address of ["not a url", "javascript:alert(1)", "data:text/html,hello", "gideon://outside"]) {
      const result = navigationDecision(address, "https://gateway.example", true);
      assert.equal(result.allowed, false); assert.equal(result.external, "");
    }
  });
});
