import React, { useEffect, useRef, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import type { ShellRoute } from "../../shared/shell/shellRoutes";
import {
  addLibraryCollectionItem, listLibraryCollections, LibraryReadError,
  searchKnowledgeItems, setLibraryFavorite, setLibraryReadState,
  type KnowledgeItem, type LibraryCollection,
} from "./libraryApi";
import { LibraryItemLink } from "./LibraryWorkspace.web";

export function LibrarySearch({ scope, route, navigate, onChanged, activeOwnerScope }: {
  scope: OwnerScope; route: ShellRoute; navigate: (route: ShellRoute) => void; onChanged?: () => void;
  activeOwnerScope: React.RefObject<string>;
}) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [kind, setKind] = useState("");
  const [provider, setProvider] = useState("");
  const [readState, setReadState] = useState("");
  const [favorite, setFavorite] = useState("");
  const [items, setItems] = useState<readonly KnowledgeItem[]>([]);
  const [collections, setCollections] = useState<readonly LibraryCollection[]>([]);
  const [collectionId, setCollectionId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const generation = useRef(0);
  const activeScope = useRef(scope.cacheKey);
  activeScope.current = scope.cacheKey;
  const isOwnerCurrent = () => activeScope.current === scope.cacheKey && activeOwnerScope.current === scope.cacheKey;

  useEffect(() => {
    const controller = new AbortController();
    const current = ++generation.current;
    const currentScope = scope.cacheKey;
    void listLibraryCollections(scope, controller.signal).then(value => {
      if (!controller.signal.aborted && generation.current === current && isOwnerCurrent() && currentScope === scope.cacheKey) setCollections(value);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted && generation.current === current && isOwnerCurrent() && currentScope === scope.cacheKey)
        setError(reason instanceof Error ? reason.message : "Collections could not be loaded.");
    });
    return () => controller.abort();
  }, [scope.cacheKey]);

  async function runSearch(event?: React.FormEvent) {
    event?.preventDefault();
    const current = ++generation.current;
    const currentScope = scope.cacheKey;
    const controller = new AbortController();
    setLoading(true); setError(undefined); setNotice(undefined);
    try {
      const result = await searchKnowledgeItems(scope, {
        query, type, kind, provider, readState,
        ...(favorite ? { favorite: favorite === "yes" } : {}),
      }, controller.signal);
      if (generation.current !== current || !isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setItems(result.items);
    } catch (reason) {
      if (generation.current !== current || !isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setError(reason instanceof Error ? reason.message : "Search failed. Retry your search.");
      setItems([]);
    } finally {
      if (generation.current === current && isOwnerCurrent() && currentScope === scope.cacheKey) setLoading(false);
    }
  }

  async function updateItem(item: KnowledgeItem, action: "favorite" | "read", value: boolean | "unread" | "reading" | "read") {
    const currentScope = scope.cacheKey;
    setBusyIds(ids => new Set(ids).add(item.id)); setNotice(undefined); setError(undefined);
    try {
      if (action === "favorite") await setLibraryFavorite(scope, item.id, value as boolean);
      else await setLibraryReadState(scope, item.id, value as "unread" | "reading" | "read");
      if (!isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setItems(itemsNow => itemsNow.map(existing => existing.id !== item.id ? existing : action === "favorite"
        ? { ...existing, favorited: value as boolean }
        : { ...existing, read_state: value as KnowledgeItem["read_state"] }));
      onChanged?.();
    } catch (reason) {
      if (!isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setError(reason instanceof LibraryReadError ? `${reason.message} Retry the update.` : "The update failed. Retry the action.");
    } finally {
      if (isOwnerCurrent() && currentScope === scope.cacheKey) setBusyIds(ids => { const next = new Set(ids); next.delete(item.id); return next; });
    }
  }

  async function addToCollection(item: KnowledgeItem) {
    const collection = collections.find(candidate => candidate.id === collectionId && candidate.kind === "manual");
    if (!collection) { setError("Choose a manual collection first."); return; }
    const currentScope = scope.cacheKey;
    setBusyIds(ids => new Set(ids).add(item.id)); setError(undefined); setNotice(undefined);
    try {
      await addLibraryCollectionItem(scope, collection.id, item.id);
      if (!isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setNotice(`Added “${item.title}” to ${collection.name}.`);
      onChanged?.();
    } catch (reason) {
      if (!isOwnerCurrent() || currentScope !== scope.cacheKey) return;
      setError(reason instanceof Error ? `${reason.message} The item was not added.` : "The item was not added. Retry the action.");
    } finally {
      if (isOwnerCurrent() && currentScope === scope.cacheKey) setBusyIds(ids => { const next = new Set(ids); next.delete(item.id); return next; });
    }
  }

  return <section aria-labelledby="library-search-title">
    <h3 id="library-search-title">Search your knowledge</h3>
    <form onSubmit={runSearch}>
      <label>Search terms <input type="search" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <label>Item type <input value={type} onChange={event => setType(event.target.value)} placeholder="Any type" /></label>
      <label>Kind <input value={kind} onChange={event => setKind(event.target.value)} placeholder="Any kind" /></label>
      <label>Provider <input value={provider} onChange={event => setProvider(event.target.value)} placeholder="Any provider" /></label>
      <label>Reading state <select value={readState} onChange={event => setReadState(event.target.value)}><option value="">Any state</option><option value="unread">Unread</option><option value="reading">Reading</option><option value="read">Read</option></select></label>
      <label>Favorite <select value={favorite} onChange={event => setFavorite(event.target.value)}><option value="">Any</option><option value="yes">Favorites</option><option value="no">Not favorites</option></select></label>
      <button type="submit" disabled={loading}>{loading ? "Searching…" : "Search"}</button>
    </form>
    {error && <p role="alert">{error}<button type="button" onClick={() => void runSearch()}>Retry</button></p>}
    {notice && <p role="status">{notice}</p>}
    {!loading && !error && items.length === 0 && <p role="status">No matching knowledge items. Try a different search or filter.</p>}
    {items.length > 0 && <>
      <p role="status">{items.length} matching {items.length === 1 ? "item" : "items"}</p>
      <label>Add results to <select aria-label="Manual collection" value={collectionId} onChange={event => setCollectionId(event.target.value)}>
        <option value="">Choose a manual collection</option>{collections.filter(collection => collection.kind === "manual").map(collection => <option key={collection.id} value={collection.id}>{collection.name}</option>)}
      </select></label>
      <ul>{items.map(item => <li key={item.id}>
        <LibraryItemLink item={item} origin={route} navigate={navigate} />
        <div className="gideon-library-curation__actions">
          <button type="button" disabled={busyIds.has(item.id)} aria-pressed={Boolean(item.favorited)} onClick={() => void updateItem(item, "favorite", !item.favorited)}>{item.favorited ? "Remove favorite" : "Add favorite"}</button>
          <label>Reading progress <select aria-label={`Reading progress for ${item.title}`} disabled={busyIds.has(item.id)} value={item.read_state || "unread"} onChange={event => void updateItem(item, "read", event.target.value as "unread" | "reading" | "read")}>
            <option value="unread">Unread</option><option value="reading">Reading</option><option value="read">Read</option>
          </select></label>
          <button type="button" disabled={busyIds.has(item.id) || !collectionId} onClick={() => void addToCollection(item)}>Add to collection</button>
        </div>
      </li>)}</ul>
    </>}
  </section>;
}
