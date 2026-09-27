import React, { useRef, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import type { ShellRoute } from "../../shared/shell/shellRoutes";
import { LibraryReadError, getKnowledgeItem, ingestKnowledgeFile, type KnowledgeItem } from "./libraryApi";
import { libraryItemRoute } from "./libraryRoutes";
import { LibraryItemLink } from "./LibraryWorkspace.web";

type ImportResult = { item: KnowledgeItem; status: string; filename: string };

export function ImportPanel({ scope, activeOwnerScope, route, navigate }: {
  scope: OwnerScope;
  activeOwnerScope: React.RefObject<string>;
  route: ShellRoute;
  navigate: (route: ShellRoute) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [result, setResult] = useState<ImportResult>();
  const [progress, setProgress] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const request = useRef(0);
  const ownerKey = scope.cacheKey;

  async function importFile(file?: File) {
    if (!file) return;
    const generation = ++request.current;
    const isCurrent = () => request.current === generation && activeOwnerScope.current === ownerKey;
    setError(undefined);
    setResult(undefined);
    setProgress("Uploading file to Gideon…");
    setBusy(true);
    try {
      const accepted = await ingestKnowledgeFile(scope, file);
      if (!isCurrent()) return;
      setProgress(accepted.status === "processing" ? "Upload received. Checking native extraction status…" : "Upload is stored in your Library.");
      const item = await getKnowledgeItem(scope, accepted.itemId);
      if (!isCurrent()) return;
      const status = item.processing_status || accepted.status;
      setResult({ item, status, filename: file.name });
      setProgress("");
    } catch (reason) {
      if (!isCurrent()) return;
      setError(reason instanceof LibraryReadError ? reason.message : "Gideon could not import this file.");
      setProgress("");
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  async function refreshStatus() {
    if (!result) return;
    const generation = request.current;
    if (activeOwnerScope.current !== ownerKey) return;
    setRefreshing(true);
    setError(undefined);
    try {
      const item = await getKnowledgeItem(scope, result.item.id);
      if (request.current === generation && activeOwnerScope.current === ownerKey) {
        setResult(current => current?.item.id === item.id ? { ...current, item, status: item.processing_status || current.status } : current);
      }
    } catch (reason) {
      if (request.current === generation && activeOwnerScope.current === ownerKey) {
        if (reason instanceof LibraryReadError && (reason.kind === "forbidden" || reason.kind === "missing")) setResult(undefined);
        setError(reason instanceof LibraryReadError ? reason.message : "Gideon could not refresh extraction status.");
      }
    } finally {
      if (request.current === generation && activeOwnerScope.current === ownerKey) setRefreshing(false);
    }
  }

  const statusText = result?.status === "queued" ? "Queued for extraction"
    : result?.status === "processing" ? "Extraction in progress"
      : result?.status === "done" || result?.status === "complete" ? "Extraction complete"
        : result?.status === "failed" || result?.status === "error" ? "Extraction failed"
          : result ? `Native status: ${result.status}` : "";

  return <section aria-labelledby="library-import-title" className="gideon-library-capture">
    <h3 id="library-import-title">Import a PDF</h3>
    <p>Gideon stores the original PDF, creates a native Library item, and reports the extraction state recorded by the knowledge service.</p>
    <label htmlFor="library-pdf-upload">PDF file</label>
    <input id="library-pdf-upload" type="file" accept="application/pdf,.pdf" disabled={busy}
      onChange={event => { const file = event.currentTarget.files?.[0]; void importFile(file); event.currentTarget.value = ""; }} />
    {busy && <p role="status" aria-live="polite">{progress}</p>}
    {error && <p role="alert">{error}</p>}
    {result && <section aria-label="Imported knowledge item" data-import-item-id={result.item.id}>
      <h4>Imported from {result.filename}</h4>
      <p role="status" data-extraction-status={result.status}>{statusText}</p>
      {result.item.provider && <p>Provider: {result.item.provider}</p>}
      {result.item.source_url && <p>Source: {result.item.source_url}</p>}
      {(result.status === "failed" || result.status === "error") && result.item.processing_error && <p role="alert">{result.item.processing_error}</p>}
      <LibraryItemLink item={result.item} origin={route} navigate={navigate} />
      <button type="button" onClick={() => { navigate(libraryItemRoute({ kind: "knowledge", id: result.item.id }, route)); }}>Open item</button>
      <button type="button" onClick={() => void refreshStatus()} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh extraction status"}</button>
    </section>}
  </section>;
}
