import { useEffect, useRef, useState, type ReactNode } from "react";
import { Hash, FileCode } from "lucide-react";
import type { TaskItem } from "../../../../console/src/shared/data/api";
import { TaskDetail } from "../../../../console/src/features/tasks/TaskDetail";
import { ContentSurface } from "../../../../console/src/shared/ui/content/ContentSurface";
import type { ContentType, PreviewProps } from "../../../../console/src/shared/ui/content/contentTypes";
import { Markdown } from "../../../../console/src/shared/ui/Markdown";
import { DialogHost } from "../../../../console/src/shared/ui/dialog/DialogHost";
import { Toaster } from "../../../../console/src/shared/ui/Toaster";
import { GatewayError, gatewayJson } from "../transport.web";
import { createShellRoute } from "./shellRoutes";
import { WorkspaceFrame } from "./WorkspaceFrame";
import { artifactRequest, type ModuleProps } from "./webModules";

function ConsoleModuleProviders({ children }: { children: ReactNode }) {
  return <>{children}<DialogHost /><Toaster /></>;
}

function ModuleRequestState({ message, retry, onReturn }: { message: string; retry?: () => void; onReturn: () => void }) {
  return <section className="grid gap-m p-l" aria-label="Workspace record">
    <p role="alert">{message}</p>
    <div className="flex flex-wrap gap-s">
      {retry && <button type="button" onClick={retry}>Retry</button>}
      <button type="button" onClick={onReturn}>Return</button>
    </div>
  </section>;
}

function taskUnavailable(error: unknown): string {
  if (error instanceof GatewayError && error.status === 404) return "This task is no longer available.";
  if (error instanceof GatewayError && error.status === 403 && !error.authRequired) return "You do not have access to this task.";
  return "Gideon could not load this task. Check your connection and retry.";
}

export function TaskModule(props: ModuleProps) {
  const id = props.route.record?.kind === "task" ? props.route.record.id : "";
  const [task, setTask] = useState<TaskItem | null>(null);
  const [failure, setFailure] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setTask(null);
    setFailure("");
    if (!id || typeof window === "undefined" || props.scope.runtimeOrigin !== window.location.origin || !props.scope.ownerId) {
      setFailure("This task cannot be checked for the current Gideon account.");
      return () => { active = false; controller.abort(); };
    }
    void gatewayJson<TaskItem>(`/api/tasks/${encodeURIComponent(id)}`, { signal: controller.signal }).then((record) => {
      if (!active) return;
      if (record?.id !== id || typeof record.title !== "string" || typeof record.status !== "string") {
        setFailure("Gideon returned an invalid task record.");
        return;
      }
      setTask(record);
    }).catch((error: unknown) => {
      if (active && !(error instanceof DOMException && error.name === "AbortError")) setFailure(taskUnavailable(error));
    });
    return () => { active = false; controller.abort(); };
  }, [id, props.scope.cacheKey, attempt]);

  const content = failure ? <ModuleRequestState message={failure} retry={() => setAttempt((value) => value + 1)} onReturn={props.onReturn} />
    : task ? <ConsoleModuleProviders><div className="min-w-0 p-m">
      <TaskDetail task={task} allTasks={[task]} editing={editing} onEditingChange={setEditing}
        onSaved={setTask} onDeleted={props.onReturn}
        onOpenTask={(nextId) => props.navigate(createShellRoute("activity", {
          view: "detail", placement: { id: "tasks" }, record: { kind: "task", id: nextId }, returnTo: props.returnTo,
        }))} />
    </div></ConsoleModuleProviders>
      : <p role="status" aria-live="polite" className="p-l">Loading task…</p>;
  return <WorkspaceFrame route={props.route} mode="full" title={task?.title ?? "Task details"}
    actions={props.returnTo && <button type="button" className="gideon-workspace-back" aria-label="Return to previous workspace" onClick={props.onReturn}>Return to previous workspace</button>}
    onGoToChat={() => props.navigate(createShellRoute("chat"))}>{content}</WorkspaceFrame>;
}

type NativeArtifact = {
  slug: string;
  name: string;
  kind: string;
  content: string | null;
  readonly?: boolean;
  description?: string;
  version?: number;
};

function MarkdownPreview({ content }: PreviewProps) {
  return <div className="px-l py-m"><Markdown>{content}</Markdown></div>;
}

const markdownType: ContentType = {
  id: "markdown",
  label: "Markdown",
  icon: Hash,
  tone: "#4f9be0",
  kinds: ["markdown"],
  preview: { render: MarkdownPreview },
  edit: { language: "markdown", split: true },
};

function artifactType(kind: string): ContentType {
  if (kind === "markdown") return markdownType;
  const language = kind === "html" ? "html" : kind === "json" ? "json"
    : kind === "svg" ? "xml" : kind === "react" ? "javascript" : "plaintext";
  return {
    id: `trusted-artifact-${kind}`,
    label: kind,
    icon: FileCode,
    tone: "var(--color-on-surface-low)",
    kinds: [kind],
    edit: { language },
  };
}

function artifactUnavailable(error: unknown): string {
  if (error instanceof GatewayError && error.status === 404) return "This artifact is no longer available.";
  if (error instanceof GatewayError && error.status === 403 && !error.authRequired) return "You do not have access to this artifact.";
  return "Gideon could not load this artifact. Check your connection and retry.";
}

export function ArtifactModule(props: ModuleProps) {
  const request = artifactRequest(props.route);
  const requestKey = `${props.scope.cacheKey}:${request?.path ?? "invalid"}`;
  const currentRequest = useRef({ key: requestKey, generation: 0 });
  if (currentRequest.current.key !== requestKey) {
    currentRequest.current = { key: requestKey, generation: currentRequest.current.generation + 1 };
  }
  const pendingSaves = useRef(new Set<AbortController>());
  const [loaded, setLoaded] = useState<{ key: string; artifact: NativeArtifact } | null>(null);
  const artifact = loaded?.key === requestKey ? loaded.artifact : null;
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [saveFailure, setSaveFailure] = useState<{ key: string; message: string } | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const generation = currentRequest.current.generation;
    let active = true;
    setLoaded(null);
    setFailure(null);
    setSaveFailure(null);
    if (!request || typeof window === "undefined" || props.scope.runtimeOrigin !== window.location.origin || !props.scope.ownerId) {
      setFailure({ key: requestKey, message: "This artifact cannot be checked for the current Gideon account." });
      return () => { active = false; controller.abort(); };
    }
    void gatewayJson<NativeArtifact>(request.path, { signal: controller.signal }).then((record) => {
      if (!active || currentRequest.current.generation !== generation) return;
      if (record?.slug !== request.slug || typeof record.name !== "string" || typeof record.kind !== "string" ||
        (request.version !== undefined && record.version !== request.version) ||
        (record.content !== null && typeof record.content !== "string")) {
        setFailure({ key: requestKey, message: "Gideon returned an invalid artifact record." });
        return;
      }
      setLoaded({ key: requestKey, artifact: record });
    }).catch((error: unknown) => {
      if (active && currentRequest.current.generation === generation &&
        !(error instanceof DOMException && error.name === "AbortError"))
        setFailure({ key: requestKey, message: artifactUnavailable(error) });
    });
    return () => {
      active = false;
      controller.abort();
      for (const save of pendingSaves.current) save.abort();
      pendingSaves.current.clear();
    };
  }, [requestKey, props.scope.ownerId, props.scope.runtimeOrigin, attempt]);

  const save = async (content: string) => {
    if (!artifact || !request || request.version !== undefined || artifact.readonly) return;
    const generation = currentRequest.current.generation;
    const controller = new AbortController();
    pendingSaves.current.add(controller);
    setSaveFailure(null);
    try {
      const updated = await gatewayJson<NativeArtifact>(request.path, {
        method: "PATCH", body: { content, snapshot: false, event_type: "edited" },
        signal: controller.signal,
      });
      if (currentRequest.current.generation !== generation) return;
      if (updated?.slug !== request.slug || typeof updated.name !== "string" || typeof updated.kind !== "string") {
        throw new Error("Gideon returned an invalid saved artifact.");
      }
      setLoaded({ key: requestKey, artifact: updated });
    } catch (error) {
      if (currentRequest.current.generation !== generation) return;
      const message = error instanceof Error ? error.message : "Gideon could not save this artifact.";
      setSaveFailure({ key: requestKey, message });
      throw error instanceof Error ? error : new Error(message);
    } finally {
      pendingSaves.current.delete(controller);
    }
  };

  const content = failure?.key === requestKey ? <ModuleRequestState message={failure.message} retry={() => setAttempt((value) => value + 1)} onReturn={props.onReturn} />
    : artifact ? <ConsoleModuleProviders><div className="flex min-h-0 h-full flex-col">
      <header className="flex min-w-0 items-center gap-s border-b border-outline/40 px-m py-2">
        <div className="min-w-0 flex-1"><h2 className="truncate text-on-surface">{artifact.name}</h2>
          <p className="truncate text-on-surface-low text-[0.75rem]">{artifact.slug} · {artifact.kind}{request?.version !== undefined ? ` · Version ${request.version} (read only)` : ""}</p></div>
        <button type="button" className="gideon-workspace-back" aria-label="Return to previous workspace" onClick={props.onReturn}>Return</button>
      </header>
      {saveFailure?.key === requestKey && <p role="alert" className="border-b border-outline/40 px-m py-2 text-[0.8125rem] text-on-surface">{saveFailure.message}</p>}
      <div className="min-h-0 flex-1">
        <ContentSurface type={artifactType(artifact.kind)} content={artifact.content ?? ""}
          title={artifact.name} docId={`${artifact.slug}:${request?.version ?? "latest"}`} language={artifactType(artifact.kind).edit?.language}
          readOnly={request?.version !== undefined || artifact.readonly} initialView="edit"
          onSave={request?.version !== undefined || artifact.readonly ? undefined : save} />
      </div>
    </div></ConsoleModuleProviders>
      : <p role="status" aria-live="polite" className="p-l">Loading artifact…</p>;
  return <WorkspaceFrame route={props.route} mode="full" title="Artifact editor"
    onGoToChat={() => props.navigate(createShellRoute("chat"))}>{content}</WorkspaceFrame>;
}
