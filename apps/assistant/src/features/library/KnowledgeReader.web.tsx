import React, { useEffect, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import { Annotations } from "./Annotations.web";
import { getLibraryItemText, LibraryReadError, type KnowledgeItem } from "./libraryApi";
import { PdfReader } from "./PdfReader.web";

type TextState = { key: string; text?: string; loading: boolean; error?: string };

export function KnowledgeReader({ scope, item, activeOwnerScope }: {
  scope: OwnerScope;
  item: KnowledgeItem;
  activeOwnerScope: React.RefObject<string>;
}) {
  const key = `${scope.cacheKey}\u0000${item.id}`;
  const [state, setState] = useState<TextState>({ key: "", loading: true });
  const current = state.key === key ? state : { key, loading: true };
  const originalName = item.file_metadata?.original_filename;
  const isPdf = item.mime_type?.toLowerCase() === "application/pdf" || originalName?.toLowerCase().endsWith(".pdf") === true;

  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState({ key, loading: true });
    void getLibraryItemText(scope, item.id, controller.signal).then(text => {
      if (live && activeOwnerScope.current === scope.cacheKey) setState({ key, text, loading: false });
    }).catch((error: unknown) => {
      if (!live || (error instanceof DOMException && error.name === "AbortError") || activeOwnerScope.current !== scope.cacheKey) return;
      setState({ key, loading: false, error: error instanceof LibraryReadError ? error.message : "The extracted text could not be opened." });
    });
    return () => { live = false; controller.abort(); };
  }, [activeOwnerScope, item.id, key, scope]);

  const text = current.text ?? item.content ?? "";
  return <article className="gideon-library-reader" aria-labelledby="library-reader-title" data-library-reader-key={key}>
    <header className="gideon-library-reader__header">
      <p className="gideon-library__eyebrow">{item.item_type ?? item.kind ?? "Knowledge item"}</p>
      <h2 id="library-reader-title">{item.title}</h2>
      <dl className="gideon-library-reader__metadata">
        <div><dt>Provider</dt><dd>{item.provider || "Gideon Knowledge"}</dd></div>
        {originalName && <div><dt>Original file</dt><dd>{originalName}</dd></div>}
        {item.source_id && <div><dt>Source record</dt><dd>{item.source_id}</dd></div>}
        {item.created_at && <div><dt>Added</dt><dd><time dateTime={item.created_at}>{item.created_at}</time></dd></div>}
        {item.source_url && <div><dt>Original source</dt><dd><a href={item.source_url} target="_blank" rel="noreferrer">Open source in a new tab</a></dd></div>}
      </dl>
    </header>
    {current.loading && <p role="status">Loading extracted text…</p>}
    {current.error && <p className="gideon-library__notice" role="alert">{current.error}</p>}
    {!current.loading && isPdf
      ? <PdfReader scope={scope} itemId={item.id} title={item.title} extractedText={text} />
      : !current.loading && <section className="gideon-library-reader__text-panel" aria-label="Document text">
        <h3>Document text</h3>
        {text ? <div id="library-extracted-text" className="gideon-library-reader__text" role="document">{text}</div>
          : <p>This item has no extracted text. Its source metadata and saved notes remain available.</p>}
      </section>}
    <Annotations scope={scope} itemId={item.id} activeOwnerScope={activeOwnerScope} />
  </article>;
}
