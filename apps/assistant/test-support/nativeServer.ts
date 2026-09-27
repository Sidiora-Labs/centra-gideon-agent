import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

export type NativeServer = Readonly<{
  child: ChildProcess;
  home: string;
  apiOrigin: string;
  controlOrigin: string;
  startup: Record<string, unknown>;
  stop: () => Promise<void>;
}>;

export async function startNativeServer(options: {
  script: string;
  origin: string;
  repositoryRoot: string;
  python?: string;
  env?: Record<string, string>;
}): Promise<NativeServer> {
  const home = await mkdtemp(join(tmpdir(), "gideon-assistant-native-"));
  const repositoryRoot = resolve(options.repositoryRoot);
  const child = spawn(options.python || process.env.GIDEON_TEST_PYTHON || "python3", [
    resolve(options.script), options.origin,
  ], {
    cwd: repositoryRoot,
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...Object.fromEntries(Object.entries(process.env).filter(([key]) => key !== "OPENAI_API_KEY")),
      ...options.env,
      GIDEON_HOME: home,
      GIDEON_TEST_MODEL: options.env?.GIDEON_TEST_MODEL ?? "",
      PYTHONPATH: join(repositoryRoot, "runtime"),
    },
  });
  let output = "";
  let errors = "";
  child.stdout.on("data", chunk => { output += String(chunk); });
  child.stderr.on("data", chunk => { errors = (errors + String(chunk)).slice(-6000); });

  try {
    const deadline = Date.now() + 30000;
    let startup: Record<string, unknown> | undefined;
    while (Date.now() < deadline && !startup) {
      const line = output.split("\n")[0];
      if (line) {
        try { startup = JSON.parse(line) as Record<string, unknown>; }
        catch (error) { throw new Error(`Native server did not emit startup JSON: ${line}; ${String(error)}`); }
        break;
      }
      if (child.exitCode !== null) throw new Error(`Native server exited ${child.exitCode}: ${errors}`);
      await new Promise(resolveWait => setTimeout(resolveWait, 50));
    }
    if (!startup) throw new Error(`Native server startup timed out: ${errors}`);
    const apiPort = startup.api_port;
    const controlPort = startup.control_port;
    if (typeof apiPort !== "number" || typeof controlPort !== "number") {
      throw new Error(`Native server startup omitted API/control ports: ${lineSummary(startup)}`);
    }
    let stopped = false;
    return {
      child,
      home,
      apiOrigin: `http://127.0.0.1:${apiPort}`,
      controlOrigin: `http://127.0.0.1:${controlPort}`,
      startup,
      stop: async () => {
        if (stopped) return;
        stopped = true;
        child.kill("SIGTERM");
        await new Promise<void>(resolveStop => {
          if (child.exitCode !== null || child.signalCode !== null) { resolveStop(); return; }
          child.once("exit", () => resolveStop());
          setTimeout(resolveStop, 5000);
        });
        await rm(home, { recursive: true, force: true });
      },
    };
  } catch (error) {
    child.kill("SIGTERM");
    await rm(home, { recursive: true, force: true });
    throw error;
  }
}

function lineSummary(value: Record<string, unknown>): string {
  return JSON.stringify(value);
}
