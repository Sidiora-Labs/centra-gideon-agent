import React, { useEffect, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import { LibraryReadError, getLibraryOriginalPdf } from "./libraryApi";

type PdfState = { key: string; status: "loading" | "ready" | "missing" | "unavailable" | "failed"; href?: string; message?: string };

export function PdfReader({ scope, itemId, title, extractedText }: {
  scope: OwnerScope;
  itemId: string;
  title: string;
  extractedText: string;
}) {
  const key = `${scope.cacheKey}\u0000${itemId}`;
  const [reload, setReload] = useState(0);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [state, setState] = useState<PdfState>({ key: "", status: "loading" });
  const current = state.key === key ? state : { key, status: "loading" as const };

  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    let objectUrl: string | undefined;
    setPreviewOpen(false);
    setState({ key, status: "loading" });
    void getLibraryOriginalPdf(scope, itemId, controller.signal).then(blob => {
      if (!live) return;
      objectUrl = URL.createObjectURL(blob);
      setState({ key, status: "ready", href: objectUrl });
    }).catch((error: unknown) => {
      if (!live || (error instanceof DOMException && error.name === "AbortError")) return;
      if (error instanceof LibraryReadError && error.kind === "missing") {
        setState({ key, status: "missing", message: "The original PDF file is unavailable." });
      } else if (error instanceof LibraryReadError && error.kind === "unavailable") {
        setState({ key, status: "unavailable", message: error.message });
      } else {
        setState({ key, status: "failed", message: error instanceof Error ? error.message : "The PDF could not be opened." });
      }
    });
    return () => {
      live = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [key, itemId, scope, reload]);

  if (current.status === "ready" && current.href) {
    return <section className="gideon-library-reader__pdf" aria-label="PDF reader" data-pdf-state="ready">
      <div className="gideon-library-reader__file-actions">
        <a href={current.href} target="_blank" rel="noreferrer">Open PDF in a new tab</a>
        <a href={current.href} download={`${title || "document"}.pdf`}>Download PDF</a>
      </div>
      {extractedText
        ? <section className="gideon-library-reader__extracted" aria-label="Extracted document text">
          <h3>Document text</h3>
          <div id="library-extracted-text" className="gideon-library-reader__text" role="document" aria-label={`Extracted text: ${title}`}>{extractedText}</div>
        </section>
        : <p className="gideon-library__notice" role="status">No extracted text is available for this item. You can open or download the original PDF.</p>}
      <details className="gideon-library-reader__original-preview" onToggle={event => setPreviewOpen(event.currentTarget.open)}>
        <summary>Preview original PDF</summary>
        {previewOpen && <iframe className="gideon-library-reader__pdf-frame" src={current.href} title={`PDF document: ${title}`} />}
      </details>
    </section>;
  }

  if (current.status === "loading") return <p className="gideon-library__notice" role="status" data-pdf-state="loading">Opening the original PDF…</p>;

  return <section className="gideon-library-reader__fallback" data-pdf-state={current.status} aria-label="Document reader">
    <p className="gideon-library__notice" role={current.status === "missing" ? "status" : "alert"}>
      {current.status === "missing" ? "The original PDF is missing. Showing the extracted text saved with this item." : `${current.message ?? "The original PDF could not be opened."} Showing the extracted text saved with this item.`}
      {current.status !== "missing" && <button type="button" onClick={() => setReload(value => value + 1)}>Retry PDF</button>}
    </p>
    {extractedText ? <div id="library-extracted-text" className="gideon-library-reader__text" role="document" aria-label={`Extracted text: ${title}`}>{extractedText}</div>
      : <p>No extracted text is available for this item.</p>}
  </section>;
}
