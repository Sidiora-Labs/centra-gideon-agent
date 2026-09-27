import React, { useEffect, useRef, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import { createLibraryAnnotation, deleteLibraryAnnotation, listLibraryAnnotations, type LibraryAnnotation, LibraryReadError } from "./libraryApi";

type AnnotationState = { key: string; values: readonly LibraryAnnotation[]; loading: boolean; error?: string };

export function Annotations({ scope, itemId, activeOwnerScope }: {
  scope: OwnerScope;
  itemId: string;
  activeOwnerScope: React.RefObject<string>;
}) {
  const key = `${scope.cacheKey}\u0000${itemId}`;
  const [state, setState] = useState<AnnotationState>({ key: "", values: [], loading: true });
  const [quote, setQuote] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [selectionMessage, setSelectionMessage] = useState("");
  const generation = useRef(0);
  const visible = state.key === key ? state : { key, values: [], loading: true };

  useEffect(() => {
    const controller = new AbortController();
    const request = ++generation.current;
    const isCurrent = () => !controller.signal.aborted && generation.current === request && activeOwnerScope.current === scope.cacheKey;
    setState({ key, values: [], loading: true });
    void listLibraryAnnotations(scope, itemId, controller.signal).then(values => {
      if (isCurrent()) setState({ key, values, loading: false });
    }).catch((error: unknown) => {
      if (!isCurrent()) return;
      setState({ key, values: [], loading: false, error: error instanceof LibraryReadError ? error.message : "Reading notes could not be loaded." });
    });
    return () => controller.abort();
  }, [activeOwnerScope, itemId, key, scope]);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const passage = quote.trim();
    if (!passage) {
      setState(current => ({ ...current, error: "Select or enter a passage before saving a note." }));
      return;
    }
    setSaving(true);
    setState(current => ({ ...current, error: undefined }));
    const request = generation.current;
    try {
      const created = await createLibraryAnnotation(scope, itemId, { quote: passage, occurrence: 0, note: note.trim() });
      if (generation.current !== request || activeOwnerScope.current !== scope.cacheKey) return;
      setState(current => current.key === key ? { ...current, values: [...current.values, created], error: undefined } : current);
      setQuote("");
      setNote("");
      setSelectionMessage("");
    } catch (error) {
      if (generation.current !== request || activeOwnerScope.current !== scope.cacheKey) return;
      setState(current => ({ ...current, error: error instanceof LibraryReadError ? error.message : "The note could not be saved. Your draft is still here." }));
    } finally {
      if (generation.current === request) setSaving(false);
    }
  }

  async function remove(annotation: LibraryAnnotation) {
    setState(current => ({ ...current, error: undefined }));
    try {
      await deleteLibraryAnnotation(scope, annotation.id);
      if (activeOwnerScope.current === scope.cacheKey) {
        setState(current => current.key === key ? { ...current, values: current.values.filter(value => value.id !== annotation.id) } : current);
      }
    } catch (error) {
      setState(current => ({ ...current, error: error instanceof LibraryReadError ? error.message : "The note could not be removed." }));
    }
  }

  function useSelection() {
    const selected = window.getSelection()?.toString().trim() ?? "";
    if (!selected) {
      setSelectionMessage("Select a passage in the extracted text first.");
      return;
    }
    setQuote(selected);
    setSelectionMessage("Selected passage added to the note.");
  }

  return <section className="gideon-library-reader__annotations" aria-labelledby="library-annotations-title" data-annotation-key={key}>
    <h3 id="library-annotations-title">Notes on this document</h3>
    {visible.loading && <p role="status">Loading saved notes…</p>}
    {visible.error && <p role="alert" data-annotation-error>{visible.error}</p>}
    {!visible.loading && !visible.values.length && !visible.error && <p>No notes on this document yet.</p>}
    {visible.values.length > 0 && <ol aria-label="Saved passage notes" className="gideon-library-reader__annotation-list">
      {visible.values.map(annotation => <li key={annotation.id} data-annotation-id={annotation.id}>
        <blockquote>{annotation.quote}</blockquote>
        {annotation.note && <p>{annotation.note}</p>}
        <button type="button" aria-label={`Remove note for ${annotation.quote.slice(0, 48)}`} onClick={() => void remove(annotation)}>Remove note</button>
      </li>)}
    </ol>}
    <form onSubmit={event => void save(event)} aria-label="Add passage note" data-annotation-draft={key}>
      <label htmlFor="library-annotation-quote">Passage</label>
      <textarea id="library-annotation-quote" value={quote} maxLength={2000} required onChange={event => setQuote(event.currentTarget.value)} />
      <button type="button" onClick={useSelection}>Use selected passage</button>
      {selectionMessage && <p role="status">{selectionMessage}</p>}
      <label htmlFor="library-annotation-note">Note</label>
      <textarea id="library-annotation-note" value={note} maxLength={4000} onChange={event => setNote(event.currentTarget.value)} />
      <button type="submit" disabled={saving || visible.loading}>{saving ? "Saving note…" : "Save note"}</button>
    </form>
  </section>;
}
