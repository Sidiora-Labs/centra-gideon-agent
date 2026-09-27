import React, { useCallback, useEffect, useRef, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import { getLibraryHome, type LibraryHome as LibraryHomeData, LibraryReadError } from "./libraryApi";
import { LibraryItemLink } from "./LibraryWorkspace.web";
import { LibrarySearch } from "./LibrarySearch.web";
import { Collections } from "./Collections.web";

type Panel = "home" | "search" | "collections";

export function LibraryHome({ scope, navigate, route }: {
  scope: OwnerScope;
  navigate: (route: import("../../shared/shell/shellRoutes").ShellRoute) => void;
  route: import("../../shared/shell/shellRoutes").ShellRoute;
}) {
  const activeOwnerScope = useRef(scope.cacheKey);
  activeOwnerScope.current = scope.cacheKey;
  return <LibraryHomeContent key={scope.cacheKey} scope={scope} navigate={navigate} route={route} activeOwnerScope={activeOwnerScope} />;
}

function LibraryHomeContent({ scope, navigate, route, activeOwnerScope }: {
  scope: OwnerScope;
  navigate: (route: import("../../shared/shell/shellRoutes").ShellRoute) => void;
  route: import("../../shared/shell/shellRoutes").ShellRoute;
  activeOwnerScope: React.RefObject<string>;
}) {
  const [panel, setPanel] = useState<Panel>("home");
  const [snapshot, setSnapshot] = useState<LibraryHomeData>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [generation, setGeneration] = useState(0);
  const request = useRef(0);
  const refresh = useCallback(() => setGeneration(value => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    const current = ++request.current;
    const isCurrent = () => !controller.signal.aborted && request.current === current && activeOwnerScope.current === scope.cacheKey;
    setBusy(true);
    void getLibraryHome(scope, controller.signal).then(data => {
      if (!isCurrent()) return;
      setSnapshot(data);
      setError(undefined);
    }).catch((reason: unknown) => {
      if (!isCurrent()) return;
      setError(reason instanceof LibraryReadError ? reason.message : "Your Library could not be loaded.");
    }).finally(() => { if (isCurrent()) setBusy(false); });
    return () => controller.abort();
  }, [scope.cacheKey, generation]);

  return <section className="gideon-library-curation" aria-labelledby="library-home-title">
    <header><p className="gideon-library__eyebrow">Research and Knowledge</p><h2 id="library-home-title">Your Library</h2>
      <nav aria-label="Library sections" className="gideon-library-curation__tabs">
        {([ ["home", "Overview"], ["search", "Search"], ["collections", "Collections"] ] as const).map(([id, label]) =>
          <button type="button" key={id} aria-pressed={panel === id} onClick={() => setPanel(id)}>{label}</button>)}
      </nav>
    </header>
    {error && <p role="alert" className="gideon-library__notice">{error} <button type="button" onClick={refresh}>Retry</button></p>}
    {panel === "home" && <>
      {busy && !snapshot && <p role="status">Loading Library…</p>}
      {snapshot && <>
        <Shelf title="Recently added" items={snapshot.recently_added} route={route} navigate={navigate} />
        <Shelf title="Continue reading" items={snapshot.continue_reading} route={route} navigate={navigate} />
        <Shelf title="Favorites" items={snapshot.favorites} route={route} navigate={navigate} />
        <section aria-labelledby="library-home-collections"><h3 id="library-home-collections">Collections</h3>
          {snapshot.collections.length ? <ul>{snapshot.collections.map(collection => <li key={collection.id}>
            <button type="button" onClick={() => setPanel("collections")}>{collection.name} · {collection.kind === "smart" ? "Smart collection" : `${collection.item_count ?? 0} items`}</button>
          </li>)}</ul> : <p>No collections yet. Create one in Collections.</p>}
        </section>
      </>}
    </>}
    {panel === "search" && <LibrarySearch key={scope.cacheKey} scope={scope} route={route} navigate={navigate} onChanged={refresh} activeOwnerScope={activeOwnerScope} />}
    {panel === "collections" && <Collections key={scope.cacheKey} scope={scope} route={route} navigate={navigate} onChanged={refresh} activeOwnerScope={activeOwnerScope} />}
  </section>;
}

function Shelf({ title, items, route, navigate }: {
  title: string; items: LibraryHomeData[keyof Pick<LibraryHomeData, "recently_added" | "continue_reading" | "favorites">];
  route: import("../../shared/shell/shellRoutes").ShellRoute;
  navigate: (route: import("../../shared/shell/shellRoutes").ShellRoute) => void;
}) {
  return <section aria-label={title}><h3>{title}</h3>{items.length ? <ul>
    {items.map(item => <li key={item.id}><LibraryItemLink item={item} origin={route} navigate={navigate} /></li>)}
  </ul> : <p>No items in this section yet.</p>}</section>;
}
