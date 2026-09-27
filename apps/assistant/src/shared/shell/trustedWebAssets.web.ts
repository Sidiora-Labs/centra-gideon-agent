import { configureMonacoWorkers } from "./monacoWorkers.web";

const stylesheetId = "gideon-trusted-console-stylesheet";
const dialogScopeClass = "gideon-trusted-module-dialog-root";
let preparation: Promise<void> | undefined;

function assistantBaseUrl(): URL {
  const configuredBase = document.querySelector("base[href]")?.getAttribute("href");
  const base = new URL(configuredBase ?? window.location.href, window.location.href);
  base.search = "";
  base.hash = "";
  if (!base.pathname.endsWith("/")) base.pathname += "/";
  return base;
}

function loadConsoleStylesheet(href: string): Promise<void> {
  const existing = document.getElementById(stylesheetId);
  if (existing instanceof HTMLLinkElement) {
    if (existing.href !== href) existing.href = href;
    if (existing.sheet) return Promise.resolve();
    return new Promise((resolve, reject) => {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error("compiled Gideon console stylesheet failed to load")), { once: true });
    });
  }

  const link = document.createElement("link");
  link.id = stylesheetId;
  link.rel = "stylesheet";
  link.href = href;
  return new Promise((resolve, reject) => {
    link.addEventListener("load", () => resolve(), { once: true });
    link.addEventListener("error", () => reject(new Error("compiled Gideon console stylesheet failed to load")), { once: true });
    document.head.append(link);
  });
}

function trackConsoleDialogScope(): void {
  const ownedPortalRoots = new Set<Element>();
  const sync = () => {
    const trustedModuleMounted = document.querySelector(".gideon-trusted-module");
    const activePortalRoots = new Set<Element>();
    if (trustedModuleMounted) {
      for (const dialog of Array.from(document.body.querySelectorAll('[role="dialog"], [role="alertdialog"]'))) {
        if (!dialog.classList.contains("max-w-[420px]")) continue;
        const portalRoot = dialog.closest(".fixed.inset-0");
        if (!portalRoot) continue;
        portalRoot.classList.add(dialogScopeClass);
        activePortalRoots.add(portalRoot);
      }
    }
    for (const root of ownedPortalRoots) if (!activePortalRoots.has(root)) root.classList.remove(dialogScopeClass);
    ownedPortalRoots.clear();
    for (const root of activePortalRoots) ownedPortalRoots.add(root);
  };

  sync();
  new MutationObserver(sync).observe(document.body, { childList: true, subtree: true });
}

export function prepareTrustedWebRuntime(): Promise<void> {
  if (preparation) return preparation;

  preparation = (async () => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      throw new Error("trusted web modules require a browser document");
    }
    const assetRoot = new URL("assets/", assistantBaseUrl());
    await Promise.all([
      loadConsoleStylesheet(new URL("gideon-console.css", assetRoot).href),
      configureMonacoWorkers(assetRoot),
    ]);
    trackConsoleDialogScope();
  })().catch((error: unknown) => {
    preparation = undefined;
    throw error;
  });

  return preparation;
}
