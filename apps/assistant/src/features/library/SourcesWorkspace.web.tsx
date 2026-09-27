import React, { useEffect, useRef, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import {
  createWatchedSource, LibraryReadError, listWatchedSources, matchSourceRecipes, updateWatchedSource,
  type SourceRecipeMatch, type WatchedSource, type WatchedSourceCatalog, type WatchedSourceKind,
} from "./libraryApi";

function sourceHealth(source: WatchedSource): { label: string; detail: string } {
  if (!source.enabled) return { label: "Paused", detail: "Polling is disabled. Resume this source when you are ready." };
  if (source.event_driven) return { label: "Event driven", detail: "This source is updated by native change events rather than polling." };
  if (!source.enrolled) return { label: "Provider unavailable", detail: "No registered provider can poll this source. Choose a currently available provider and create a replacement." };
  if (!source.last_poll_at) return { label: "Not checked yet", detail: "The source is saved, but the provider has not reported a poll result." };
  const health = source.health_status || "never polled";
  if (health === "ok") return { label: "Healthy", detail: source.last_poll_at ? `Last checked ${source.last_poll_at}.` : "The native provider reports a successful poll." };
  if (health === "error" || health === "needs render tier" || health === "needs browse tier") {
    const detail = source.last_error_summary || source.remediation?.guidance || "The last native poll reported a failure.";
    return { label: health === "error" ? "Failed" : health, detail: `${detail} Check the saved source settings before resuming.` };
  }
  if (health === "degraded") return { label: "Degraded", detail: source.last_error_summary || "The provider reported a partial result." };
  return { label: "Unknown health", detail: "The native provider returned an unrecognized health state. Refresh the source list or inspect the native service." };
}

function sourceSpec(kind: WatchedSourceKind, location: string): Record<string, unknown> {
  if (kind.form === "feed") return { kind: kind.formats?.includes("rss") ? "rss" : kind.formats?.[0] || "rss", url: location.trim() };
  if (kind.form === "dir") return { path: location.trim() };
  return { url: location.trim() };
}

export function SourcesWorkspace({ scope, activeOwnerScope }: {
  scope: OwnerScope;
  activeOwnerScope: React.RefObject<string>;
}) {
  const ownerKey = scope.cacheKey;
  const [catalog, setCatalog] = useState<WatchedSourceCatalog>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [location, setLocation] = useState("");
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("");
  const [matches, setMatches] = useState<readonly SourceRecipeMatch[]>([]);
  const [selectedRecipe, setSelectedRecipe] = useState<SourceRecipeMatch>();
  const [busy, setBusy] = useState(false);
  const [editingSourceId, setEditingSourceId] = useState<string>();
  const [replacementLocation, setReplacementLocation] = useState("");
  const [generation, setGeneration] = useState(0);
  const catalogRequest = useRef(0);
  const recipeRequest = useRef(0);
  const writeRequest = useRef(0);
  const writeInFlight = useRef(false);

  useEffect(() => {
    catalogRequest.current += 1;
    recipeRequest.current += 1;
    writeRequest.current += 1;
    writeInFlight.current = false;
    setBusy(false);
    setCatalog(undefined);
    setError(undefined);
    setLocation("");
    setName("");
    setProvider("");
    setMatches([]);
    setSelectedRecipe(undefined);
    setEditingSourceId(undefined);
    setReplacementLocation("");
  }, [ownerKey]);

  useEffect(() => {
    const controller = new AbortController();
    const current = ++catalogRequest.current;
    const isCurrent = () => !controller.signal.aborted && catalogRequest.current === current && activeOwnerScope.current === ownerKey;
    setLoading(true);
    void listWatchedSources(scope, controller.signal).then(value => {
      if (!isCurrent()) return;
      setCatalog(value);
      setProvider(value.kinds[0]?.provider ?? "");
      setError(undefined);
    }).catch(reason => {
      if (!isCurrent()) return;
      setCatalog(undefined);
      setError(reason instanceof LibraryReadError ? reason.message : "Watched sources could not be loaded.");
    }).finally(() => { if (isCurrent()) setLoading(false); });
    return () => controller.abort();
  }, [scope.cacheKey, generation]);

  useEffect(() => {
    const url = location.trim();
    if (!/^https?:\/\//i.test(url)) { setMatches([]); setSelectedRecipe(undefined); return; }
    const controller = new AbortController();
    const current = ++recipeRequest.current;
    const timer = setTimeout(() => {
      void matchSourceRecipes(scope, url, controller.signal).then(value => {
        if (recipeRequest.current === current && activeOwnerScope.current === ownerKey) {
          setMatches(value);
          setSelectedRecipe(undefined);
        }
      }).catch(() => {
        if (!controller.signal.aborted && recipeRequest.current === current && activeOwnerScope.current === ownerKey) setMatches([]);
      });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [location, scope.cacheKey]);

  const sourceKinds = catalog?.kinds ?? [];
  const selectedKind = sourceKinds.find(kind => kind.provider === provider);

  async function addSource(event: React.FormEvent) {
    event.preventDefault();
    if (writeInFlight.current || (!selectedKind && !selectedRecipe)) return;
    writeInFlight.current = true;
    const current = ++writeRequest.current;
    const providerName = selectedRecipe?.provider ?? provider;
    const kindName = selectedRecipe?.kind ?? selectedKind?.kind ?? "";
    const spec = selectedRecipe?.spec ?? (selectedKind ? sourceSpec(selectedKind, location) : {});
    const displayName = name.trim() || location.trim();
    const isCurrent = () => writeRequest.current === current && activeOwnerScope.current === ownerKey;
    setBusy(true);
    setError(undefined);
    try {
      const created = await createWatchedSource(scope, { name: displayName, provider: providerName, kind: kindName, spec });
      if (!isCurrent()) return;
      setLocation("");
      setName("");
      setSelectedRecipe(undefined);
      setGeneration(value => value + 1);
      setError(undefined);
      setCatalog(value => value ? { ...value, sources: [...value.sources, created] } : value);
    } catch (reason) {
      if (isCurrent()) setError(reason instanceof LibraryReadError ? reason.message : "Gideon could not create this watched source.");
    } finally {
      if (isCurrent()) {
        writeInFlight.current = false;
        setBusy(false);
      }
    }
  }

  async function saveSource(source: WatchedSource, input: { enabled?: boolean; spec?: Record<string, unknown>; budget?: Record<string, unknown> }) {
    if (writeInFlight.current) return;
    writeInFlight.current = true;
    const current = ++writeRequest.current;
    setBusy(true);
    setError(undefined);
    try {
      const saved = await updateWatchedSource(scope, source.id, input);
      if (writeRequest.current === current && activeOwnerScope.current === ownerKey) {
        setCatalog(value => value ? { ...value, sources: value.sources.map(row => row.id === saved.id ? saved : row) } : value);
        setEditingSourceId(undefined);
        setReplacementLocation("");
      }
    } catch (reason) {
      if (writeRequest.current === current && activeOwnerScope.current === ownerKey) setError(reason instanceof LibraryReadError ? reason.message : "Gideon could not update this watched source.");
    } finally {
      if (writeRequest.current === current && activeOwnerScope.current === ownerKey) {
        writeInFlight.current = false;
        setBusy(false);
      }
    }
  }

  return <section aria-labelledby="library-sources-title" className="gideon-library-sources">
    <h3 id="library-sources-title">Watched sources</h3>
    <p>Provider availability, poll health, and remediation come from Gideon’s knowledge service. Saving a source does not mean it has been polled.</p>
    {error && <p role="alert">{error} <button type="button" onClick={() => setGeneration(value => value + 1)}>Retry</button></p>}
    {loading && !catalog && <p role="status">Loading native source providers…</p>}
    {catalog && sourceKinds.length === 0 && <p role="status">No watched-source providers are currently registered. Check the knowledge service before adding a source.</p>}
    {catalog && sourceKinds.length > 0 && <form onSubmit={event => void addSource(event)} aria-label="Add watched source">
      <h4>Add a source</h4>
      <label htmlFor="library-source-name">Name</label>
      <input id="library-source-name" value={name} onChange={event => setName(event.currentTarget.value)} placeholder="Optional name" disabled={busy} />
      <label htmlFor="library-source-provider">Available provider</label>
      <select id="library-source-provider" value={selectedRecipe?.provider ?? provider} onChange={event => { setProvider(event.currentTarget.value); setSelectedRecipe(undefined); }} disabled={busy}>
        {sourceKinds.map(kind => <option key={kind.provider} value={kind.provider}>{kind.display_name}</option>)}
      </select>
      <label htmlFor="library-source-location">{selectedKind?.form === "dir" ? "Directory path" : selectedKind?.form === "feed" ? "Feed URL" : "Listing page URL"}</label>
      <input id="library-source-location" value={location} onChange={event => { setLocation(event.currentTarget.value); setSelectedRecipe(undefined); }}
        type={selectedKind?.form === "dir" ? "text" : "url"} required autoComplete="url" disabled={busy} />
      {matches.length > 0 && <fieldset><legend>Matching native source recipes</legend>
        {matches.map(match => <label key={match.id}><input type="radio" name="library-source-recipe" checked={selectedRecipe?.id === match.id} disabled={busy}
          onChange={() => { setSelectedRecipe(match); setProvider(match.provider); }} />{match.displayName} — {match.description}</label>)}
      </fieldset>}
      {selectedKind?.form === "feed" && <p>New feed sources start with the {selectedKind.formats?.includes("rss") ? "RSS" : selectedKind.formats?.[0] ?? "configured"} parser. Validation and later poll health come from the native provider.</p>}
      {selectedKind?.form === "dir" && <p>The path is read by Gideon’s native directory provider. The service must be able to access it.</p>}
      <button type="submit" disabled={busy || (!selectedKind && !selectedRecipe)}>{busy ? "Saving…" : "Save watched source"}</button>
    </form>}
    {catalog && <section aria-label="Saved watched sources">
      <h4>Saved sources</h4>
      {catalog.sources.length === 0 ? <p>No watched sources yet.</p> : <ul>
        {catalog.sources.map(source => {
          const health = sourceHealth(source);
          const recipeAction = source.remediation?.action;
          return <li key={source.id} data-source-id={source.id}>
            <h5>{source.name}</h5><p>{source.provider} · {health.label}</p><p>{health.detail}</p>
            {source.remediation?.guidance && <p>{source.remediation.guidance}</p>}
            {source.remediation?.detail && <p>{source.remediation.detail}</p>}
            {recipeAction === "allow_render" && <button type="button" disabled={busy} onClick={() => void saveSource(source, { budget: { ...(source.budget ?? {}), allow_render: true } })}>Allow native render tier</button>}
            {recipeAction === "edit_url" && <button type="button" disabled={busy} onClick={() => { setEditingSourceId(source.id); setReplacementLocation(String(source.spec.url ?? "")); }}>Edit listing URL</button>}
            {editingSourceId === source.id && <form onSubmit={event => { event.preventDefault(); void saveSource(source, { spec: { ...source.spec, url: replacementLocation.trim() } }); }}>
              <label htmlFor={`library-source-url-${source.id}`}>Listing URL</label><input id={`library-source-url-${source.id}`} type="url" required value={replacementLocation} onChange={event => setReplacementLocation(event.currentTarget.value)} disabled={busy} />
              <button type="submit" disabled={busy}>Save URL</button><button type="button" disabled={busy} onClick={() => setEditingSourceId(undefined)}>Cancel</button>
            </form>}
            {!source.event_driven && source.enrolled && <button type="button" disabled={busy} onClick={() => void saveSource(source, { enabled: !source.enabled })}>{source.enabled ? "Pause source" : "Resume source"}</button>}
          </li>;
        })}
      </ul>}
    </section>}
  </section>;
}
