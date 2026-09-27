export type MonacoWorkerFamily = "editor" | "json" | "css" | "html" | "typescript";

const workerFiles: Record<MonacoWorkerFamily, string> = {
  editor: "gideon-monaco-editor.worker.js",
  json: "gideon-monaco-json.worker.js",
  css: "gideon-monaco-css.worker.js",
  html: "gideon-monaco-html.worker.js",
  typescript: "gideon-monaco-typescript.worker.js",
};

const languageFamilies: ReadonlyArray<readonly [MonacoWorkerFamily, readonly string[]]> = [
  ["json", ["json"]],
  ["css", ["css", "scss", "less"]],
  ["html", ["html", "handlebars", "razor"]],
  ["typescript", ["typescript", "javascript"]],
];

export function monacoWorkerFamily(label: string): MonacoWorkerFamily {
  return languageFamilies.find(([, labels]) => labels.includes(label))?.[0] ?? "editor";
}

export async function configureMonacoWorkers(assetRoot: URL): Promise<void> {
  const urls = Object.fromEntries(Object.entries(workerFiles).map(([family, file]) => [family, new URL(`workers/${file}`, assetRoot).href])) as Record<MonacoWorkerFamily, string>;

  await Promise.all(Object.entries(urls).map(async ([family, url]) => {
    const response = await fetch(url, { credentials: "same-origin", cache: "force-cache" });
    if (!response.ok || (response.headers.get("content-type") ?? "").toLowerCase().includes("text/html")) {
      throw new Error(`Monaco ${family} worker asset is unavailable (${response.status})`);
    }
    if ((await response.arrayBuffer()).byteLength === 0) throw new Error(`Monaco ${family} worker asset is empty`);
  }));

  const environment = {
    getWorker(_moduleId: string, label: string): Worker {
      const family = monacoWorkerFamily(label);
      return new Worker(urls[family], { type: "module", name: `gideon-monaco-${family}` });
    },
  };
  Object.assign(globalThis, { MonacoEnvironment: environment });
}
