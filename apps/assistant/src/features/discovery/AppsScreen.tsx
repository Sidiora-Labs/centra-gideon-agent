import * as React from "react";
import { useEffect, useMemo, useState } from "react";
import type { OwnerScope } from "../../shared/auth.web";
import { WorkspaceFrame } from "../../shared/shell/WorkspaceFrame.web";
import { useShellTheme } from "../../shared/shell/shellTheme";
import type { ShellReturnContext, ShellRoute } from "../../shared/shell/shellRoutes";
import {
  APP_CATEGORIES, classifyInstalledAppsError, readInstalledApps, searchApps,
  type AppAvailabilityFilter, type AppCategoryFilter, type AppSourceFilter, type InstalledApp,
  type InstalledAppsLoadError,
} from "./appSearch";

type LoadState =
  | { phase: "loading" }
  | { phase: "ready"; apps: readonly InstalledApp[] }
  | { phase: InstalledAppsLoadError };

export type AppsScreenProps = Readonly<{
  route: ShellRoute;
  scope: OwnerScope;
  navigate: (route: ShellRoute) => void;
  returnTo?: ShellReturnContext;
  onReturn: () => void;
}>;

function returnContext(route: ShellRoute): ShellReturnContext {
  return {
    destination: route.destination,
    ...(route.record ? { record: route.record } : {}),
    ...(route.placement ? { placement: route.placement } : {}),
    ...(route.sessionId ? { sessionId: route.sessionId } : {}),
  };
}

function installedAppHref(name: string): string {
  const query = new URLSearchParams({ view: "library", open: name });
  return `/#/apps/manage?${query.toString()}`;
}

export default function AppsScreen({ route, scope, navigate, returnTo, onReturn }: AppsScreenProps) {
  const { palette } = useShellTheme();
  const controlStyle: React.CSSProperties = {
    border: `1px solid ${palette.line}`, borderRadius: 10, background: palette.card,
    color: palette.text, font: "inherit", minHeight: 42, padding: "8px 12px",
  };
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<AppCategoryFilter>("All");
  const [source, setSource] = useState<AppSourceFilter>("all");
  const [availability, setAvailability] = useState<AppAvailabilityFilter>("all");
  const [loaded, setLoaded] = useState<{ scopeKey: string; state: LoadState }>(
    { scopeKey: scope.cacheKey, state: { phase: "loading" } });
  const [refresh, setRefresh] = useState(0);
  const load: LoadState = loaded.scopeKey === scope.cacheKey ? loaded.state : { phase: "loading" };

  useEffect(() => {
    const controller = new AbortController();
    setLoaded({ scopeKey: scope.cacheKey, state: { phase: "loading" } });
    readInstalledApps(scope, controller.signal).then(
      apps => { if (!controller.signal.aborted) setLoaded({ scopeKey: scope.cacheKey, state: { phase: "ready", apps } }); },
      error => { if (!controller.signal.aborted) setLoaded({ scopeKey: scope.cacheKey,
        state: { phase: classifyInstalledAppsError(error) } }); },
    );
    return () => controller.abort();
  }, [scope.cacheKey, refresh]);

  const apps = load.phase === "ready" ? load.apps : [];
  const results = useMemo(() => searchApps({ query, category, source, availability }, apps),
    [query, category, source, availability, apps]);
  const installedResultsIncomplete = load.phase !== "ready" && source !== "placements"
    && (category === "All" || category === "Installed apps") && availability === "all";
  const filtersActive = query.trim() !== "" || category !== "All" || source !== "all" || availability !== "all";
  const clearFilters = () => { setQuery(""); setCategory("All"); setSource("all"); setAvailability("all"); };

  const stateMessage = load.phase === "loading" ? "Loading installed apps. You can still browse destinations."
    : load.phase === "denied" ? "You do not have permission to view installed apps. Destinations remain visible."
    : load.phase === "unavailable" ? "The installed app collection is unavailable. Destinations remain visible."
    : load.phase === "error" ? "Installed apps could not be loaded. Destinations remain visible."
    : load.phase === "ready" && load.apps.length === 0 ? "No apps are installed. Browse destinations or manage apps in the console."
    : null;

  return <WorkspaceFrame route={route} mode="full" title="Apps" onBack={returnTo || route.returnTo ? onReturn : undefined}>
    <div style={{ margin: "0 auto", maxWidth: 1100, padding: "20px clamp(16px, 4vw, 36px) 48px" }}>
      <p style={{ color: palette.muted, marginTop: 0 }}>Explore Gideon destinations and apps installed for this owner.</p>
      <div role="search" aria-label="Search Apps" style={{ display: "grid", gap: 12,
        gridTemplateColumns: "repeat(auto-fit, minmax(min(220px, 100%), 1fr))", marginBottom: 16 }}>
        <label style={{ display: "grid", gap: 5 }}>Search destinations and installed apps
          <input type="search" value={query} onChange={event => setQuery(event.target.value)}
            placeholder="Search by name or topic" aria-controls="apps-results" style={controlStyle} />
        </label>
        <label style={{ display: "grid", gap: 5 }}>Category
          <select value={category} onChange={event => setCategory(event.target.value as AppCategoryFilter)}
            aria-controls="apps-results" style={controlStyle}>
            <option value="All">All categories</option>
            {APP_CATEGORIES.map(name => <option key={name} value={name}>{name}</option>)}
            <option value="Installed apps">Installed apps</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: 5 }}>Source
          <select value={source} onChange={event => setSource(event.target.value as AppSourceFilter)}
            aria-controls="apps-results" style={controlStyle}>
            <option value="all">Destinations and installed apps</option>
            <option value="placements">Destinations</option>
            <option value="installed">Installed apps</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: 5 }}>Destination availability
          <select value={availability} onChange={event => setAvailability(event.target.value as AppAvailabilityFilter)}
            aria-controls="apps-results" style={controlStyle}>
            <option value="all">All availability states</option>
            <option value="pending">Not available yet</option>
            <option value="ready">Available</option>
            <option value="unavailable">Unavailable</option>
          </select>
        </label>
      </div>
      <p role="status" aria-live="polite" aria-atomic="true" style={{ color: palette.muted }}>
        {results.length} {results.length === 1 ? "result" : "results"}
        {installedResultsIncomplete ? "; installed app results are incomplete" : ""}.
      </p>
      {stateMessage && <div role={load.phase === "denied" || load.phase === "error" ? "alert" : "status"}
        aria-busy={load.phase === "loading"} style={{ borderRadius: 12, background: palette.sky,
          color: palette.text, padding: 16, marginBottom: 16 }}>
        {stateMessage}
        {(load.phase === "error" || load.phase === "unavailable") && <button type="button"
          onClick={() => setRefresh(value => value + 1)} style={{ ...controlStyle, marginLeft: 12 }}>Retry installed apps</button>}
      </div>}
      {results.length === 0 ? <div role="status" style={{ padding: 20, border: `1px solid ${palette.line}`,
        borderRadius: 14, background: palette.card, color: palette.text }}>
        {installedResultsIncomplete ? "No destination matches. Installed app results are still unavailable."
          : filtersActive ? "No results match these search and filter choices." : "No destinations or installed apps are available."}
        {filtersActive && <button type="button" onClick={clearFilters} style={{ ...controlStyle, marginLeft: 12 }}>Clear search and filters</button>}
      </div> : <ul id="apps-results" aria-label="Apps search results" style={{ listStyle: "none", padding: 0, margin: 0,
        display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fill, minmax(min(260px, 100%), 1fr))" }}>
        {results.map(result => <li key={result.key} style={{ minWidth: 0, border: `1px solid ${palette.line}`, borderRadius: 14,
          background: palette.card, color: palette.text, padding: 17, display: "grid", gap: 7, alignContent: "start" }}>
          <span style={{ fontSize: 12, color: palette.muted }}>{result.category} · {result.kind === "installed" ? "Installed app" : "Destination"}</span>
          <strong style={{ fontSize: 17, color: palette.text }}>{result.label}</strong>
          {result.kind === "placement" ? <>
            <span style={{ fontSize: 13, color: palette.muted }}>
              {result.entry.availability.state === "pending" ? "Not available yet"
                : result.entry.availability.state === "unavailable" ? "Unavailable" : "Available"}
            </span>
            {result.entry.availability.state === "ready" ? <button type="button" style={controlStyle}
              onClick={() => navigate({ ...result.entry.route, returnTo: returnContext(route) })}>
              Open {result.label}
            </button> : <span style={{ fontSize: 13, color: palette.muted }}>
              {result.entry.availability.state === "pending" ? "This destination is not available yet."
                : "This destination cannot be opened right now."}
            </span>}
          </> : <>
            <span style={{ fontSize: 13, color: palette.muted }}>
              {result.app.enabled ? "Enabled" : "Disabled"} · {result.app.hasUI ? "Has app pages" : "No app pages"}
            </span>
            <a href={installedAppHref(result.app.name)} style={{ color: palette.blueDark, fontWeight: 600 }}>
              Manage {result.label} in console
            </a>
          </>}
        </li>)}
      </ul>}
    </div>
  </WorkspaceFrame>;
}
