import { useEffect, useState, type ReactNode } from "react";
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
import type { ModuleProps } from "./webModules";

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
  const slug = props.route.record?.kind === "artifact" ? props.route.record.id : "";
  const [artifact, setArtifact] = useState<NativeArtifact | null>(null);
  const [failure, setFailure] = useState("");
  const [saveFailure, setSaveFailure] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setArtifact(null);
    setFailure("");
    if (!slug || typeof window === "undefined" || props.scope.runtimeOrigin !== window.location.origin || !props.scope.ownerId) {
      setFailure("This artifact cannot be checked for the current Gideon account.");
      return () => { active = false; controller.abort(); };
    }
    void gatewayJson<NativeArtifact>(`/api/artifacts/${encodeURIComponent(slug)}`, { signal: controller.signal }).then((record) => {
      if (!active) return;
      if (record?.slug !== slug || typeof record.name !== "string" || typeof record.kind !== "string" ||
        (record.content !== null && typeof record.content !== "string")) {
        setFailure("Gideon returned an invalid artifact record.");
        return;
      }
      setArtifact(record);
    }).catch((error: unknown) => {
      if (active && !(error instanceof DOMException && error.name === "AbortError")) setFailure(artifactUnavailable(error));
    });
    return () => { active = false; controller.abort(); };
  }, [slug, props.scope.cacheKey, attempt]);

  const save = async (content: string) => {
    if (!artifact || artifact.readonly) return;
    setSaveFailure("");
    try {
      const updated = await gatewayJson<NativeArtifact>(`/api/artifacts/${encodeURIComponent(slug)}`, {
        method: "PATCH", body: { content, snapshot: false, event_type: "edited" },
      });
      if (updated?.slug !== slug || typeof updated.name !== "string" || typeof updated.kind !== "string") {
        throw new Error("Gideon returned an invalid saved artifact.");
      }
      setArtifact(updated);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Gideon could not save this artifact.";
      setSaveFailure(message);
      throw error instanceof Error ? error : new Error(message);
    }
  };

  const content = failure ? <ModuleRequestState message={failure} retry={() => setAttempt((value) => value + 1)} onReturn={props.onReturn} />
    : artifact ? <ConsoleModuleProviders><div className="flex min-h-0 h-full flex-col">
      <header className="flex min-w-0 items-center gap-s border-b border-outline/40 px-m py-2">
        <div className="min-w-0 flex-1"><h2 className="truncate text-on-surface">{artifact.name}</h2>
          <p className="truncate text-on-surface-low text-[0.75rem]">{artifact.slug} · {artifact.kind}</p></div>
        <button type="button" className="gideon-workspace-back" aria-label="Return to previous workspace" onClick={props.onReturn}>Return</button>
      </header>
      {saveFailure && <p role="alert" className="border-b border-outline/40 px-m py-2 text-[0.8125rem] text-on-surface">{saveFailure}</p>}
      <div className="min-h-0 flex-1">
        <ContentSurface type={artifactType(artifact.kind)} content={artifact.content ?? ""}
          title={artifact.name} docId={artifact.slug} language={artifactType(artifact.kind).edit?.language}
          readOnly={artifact.readonly} initialView="edit" onSave={artifact.readonly ? undefined : save} />
      </div>
    </div></ConsoleModuleProviders>
      : <p role="status" aria-live="polite" className="p-l">Loading artifact…</p>;
  return <WorkspaceFrame route={props.route} mode="full" title="Artifact editor"
    onGoToChat={() => props.navigate(createShellRoute("chat"))}>{content}</WorkspaceFrame>;
}
