import React, { useEffect, useRef, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import type { ShellRoute } from "../../shared/shell/shellRoutes";
import {
  createLibraryCollection, listLibraryCollections, LibraryReadError, removeLibraryCollectionItem,
  resolveLibraryCollection, type LibraryCollection, type KnowledgeItem,
} from "./libraryApi";
import { LibraryItemLink } from "./LibraryWorkspace.web";

export function Collections({ scope, route, navigate, onChanged, activeOwnerScope }: {
  scope: OwnerScope; route: ShellRoute; navigate: (route: ShellRoute) => void; onChanged?: () => void;
  activeOwnerScope: React.RefObject<string>;
}) {
  const [collections, setCollections] = useState<readonly LibraryCollection[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [items, setItems] = useState<readonly KnowledgeItem[]>([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"manual" | "smart">("manual");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const collectionGeneration = useRef(0);
  const itemsGeneration = useRef(0);
  const activeScope = useRef(scope.cacheKey);
  activeScope.current = scope.cacheKey;
  const isOwnerCurrent = () => activeScope.current === scope.cacheKey && activeOwnerScope.current === scope.cacheKey;

  useEffect(() => {
    const controller = new AbortController();
    const current = ++collectionGeneration.current;
    const currentScope = scope.cacheKey;
    setLoading(true);
    void listLibraryCollections(scope, controller.signal).then(value => {
      if (controller.signal.aborted || collectionGeneration.current !== current || !isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setCollections(value);
      if (!value.some(collection => collection.id === selectedId)) setSelectedId(value[0]?.id ?? "");
      setError(undefined);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted && collectionGeneration.current === current && isOwnerCurrent() && currentScope === scope.cacheKey)
        setError(reason instanceof Error ? reason.message : "Collections could not be loaded.");
    }).finally(() => {
      if (!controller.signal.aborted && collectionGeneration.current === current && isOwnerCurrent() && currentScope === scope.cacheKey) setLoading(false);
    });
    return () => controller.abort();
  }, [scope.cacheKey]);

  useEffect(() => {
    if (!selectedId) { setItems([]); return; }
    const controller = new AbortController();
    const current = ++itemsGeneration.current;
    const currentScope = scope.cacheKey;
    setLoading(true); setError(undefined);
    void resolveLibraryCollection(scope, selectedId, controller.signal).then(result => {
      if (controller.signal.aborted || itemsGeneration.current !== current || !isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setItems(result.items);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted && itemsGeneration.current === current && isOwnerCurrent() && currentScope === scope.cacheKey) {
        setItems([]); setError(reason instanceof Error ? reason.message : "This collection could not be opened.");
      }
    }).finally(() => {
      if (!controller.signal.aborted && itemsGeneration.current === current && isOwnerCurrent() && currentScope === scope.cacheKey) setLoading(false);
    });
    return () => controller.abort();
  }, [scope.cacheKey, selectedId]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName || (kind === "smart" && !query.trim())) {
      setError(kind === "smart" ? "Enter a name and a search query for this smart collection." : "Enter a name for this collection.");
      return;
    }
    const currentScope = scope.cacheKey;
    setSaving(true); setError(undefined); setNotice(undefined);
    try {
      const collection = await createLibraryCollection(scope, { name: trimmedName, kind, ...(kind === "smart" ? { query: query.trim() } : {}) });
      if (!isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setCollections(values => [...values, collection]); setName(""); setQuery(""); setSelectedId(collection.id);
      setNotice(`${collection.kind === "smart" ? "Smart" : "Manual"} collection created.`); onChanged?.();
    } catch (reason) {
      if (isOwnerCurrent() && currentScope === scope.cacheKey) setError(reason instanceof LibraryReadError ? reason.message : "The collection was not created. Retry.");
    } finally { if (isOwnerCurrent() && currentScope === scope.cacheKey) setSaving(false); }
  }

  async function remove(item: KnowledgeItem) {
    const collection = collections.find(value => value.id === selectedId);
    if (!collection || collection.kind === "smart") return;
    const currentScope = scope.cacheKey;
    const before = items;
    setSaving(true); setError(undefined); setNotice(undefined);
    try {
      await removeLibraryCollectionItem(scope, collection.id, item.id);
      if (!isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setItems(values => values.filter(value => value.id !== item.id)); setNotice(`Removed “${item.title}” from ${collection.name}.`); onChanged?.();
    } catch (reason) {
      if (!isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setItems(before); setError(reason instanceof Error ? `${reason.message} The collection is unchanged.` : "The collection is unchanged. Retry the action.");
    } finally { if (isOwnerCurrent() && currentScope === scope.cacheKey) setSaving(false); }
  }

  const selected = collections.find(collection => collection.id === selectedId);
  return <section aria-labelledby="library-collections-title">
    <h3 id="library-collections-title">Collections</h3>
    <form onSubmit={create}>
      <h4>Create a collection</h4>
      <label>Name <input value={name} onChange={event => setName(event.target.value)} maxLength={80} required /></label>
      <label>Collection type <select value={kind} onChange={event => setKind(event.target.value as "manual" | "smart")}><option value="manual">Manual</option><option value="smart">Smart</option></select></label>
      {kind === "smart" && <label>Search query <input value={query} onChange={event => setQuery(event.target.value)} required /></label>}
      <button type="submit" disabled={saving}>{saving ? "Saving…" : "Create collection"}</button>
    </form>
    {collections.length > 0 && <label>Open collection <select value={selectedId} onChange={event => setSelectedId(event.target.value)}>
      {collections.map(collection => <option key={collection.id} value={collection.id}>{collection.name} · {collection.kind === "smart" ? "Smart" : "Manual"}</option>)}
    </select></label>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {loading && <p role="status">Loading collection…</p>}
    {!loading && selected && items.length === 0 && <p role="status">This collection has no matching items.</p>}
    {!loading && !selected && collections.length === 0 && <p>No collections yet. Create a manual or smart collection above.</p>}
    {selected && selected.kind === "smart" && <p>Results update from this saved search whenever you open it: “{selected.query}”</p>}
    {items.length > 0 && <ul>{items.map(item => <li key={item.id}>
      <LibraryItemLink item={item} origin={route} navigate={navigate} />
      {selected?.kind === "manual" && <button type="button" disabled={saving} onClick={() => void remove(item)}>Remove from collection</button>}
    </li>)}</ul>}
  </section>;
}
