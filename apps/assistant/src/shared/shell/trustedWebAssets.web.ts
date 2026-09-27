import { configureMonacoWorkers } from "./monacoWorkers.web";

const stylesheetId = "gideon-trusted-console-stylesheet";
const dialogScopeClass = "gideon-trusted-module-dialog-root";
let preparation: Promise<void> | undefined;

function assistantBaseUrl(): URL {
  return new URL("/assistant/", window.location.origin);
}

function loadConsoleStylesheet(href: string): Promise<void> {
  const waitForStylesheet = (link: HTMLLinkElement) => new Promise<void>((resolve, reject) => {
    const loaded = () => {
      link.removeEventListener("load", loaded);
      link.removeEventListener("error", failed);
      resolve();
    };
    const failed = () => {
      link.removeEventListener("load", loaded);
      link.removeEventListener("error", failed);
      link.remove();
      reject(new Error("compiled Gideon console stylesheet failed to load"));
    };
    link.addEventListener("load", loaded, { once: true });
    link.addEventListener("error", failed, { once: true });
  });

  const existing = document.getElementById(stylesheetId);
  if (existing instanceof HTMLLinkElement) {
    if (existing.href !== href) existing.href = href;
    if (existing.sheet) return Promise.resolve();
    return waitForStylesheet(existing);
  }

  const link = document.createElement("link");
  link.id = stylesheetId;
  link.rel = "stylesheet";
  link.href = href;
  const loaded = waitForStylesheet(link);
  document.head.append(link);
  return loaded;
}

function trackConsoleDialogScope(): void {
  const doc = document;
  const ownedPortalRoots = new Set<Element>();
  const sync = () => {
    const trustedModuleMounted = doc.querySelector(".gideon-trusted-module");
    const activePortalRoots = new Set<Element>();
    if (trustedModuleMounted) {
      for (const portalRoot of doc.body.querySelectorAll("[data-gideon-dialog-portal]")) {
        portalRoot.classList.add(dialogScopeClass);
        activePortalRoots.add(portalRoot);
      }
    }
    for (const root of ownedPortalRoots) if (!activePortalRoots.has(root)) root.classList.remove(dialogScopeClass);
    ownedPortalRoots.clear();
    for (const root of activePortalRoots) ownedPortalRoots.add(root);
  };

  sync();
  new MutationObserver(sync).observe(doc.body, { childList: true, subtree: true });
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
