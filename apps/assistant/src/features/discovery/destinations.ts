import { createShellRoute, parseShellRoute, serializeShellRoute, type ShellDestination, type ShellRoute } from "../../shared/shell/shellRoutes";
import type { WorkspaceFrameMode } from "../../shared/shell/WorkspaceFrame.web";

export type DestinationCategory = "Chat" | "Work" | "Create" | "Library" | "Personal" | "Inbox" | "Apps" | "Settings" | "Getting started";
export type DestinationOwner = "activity" | "code" | "communications" | "conversation" | "delivery" | "discovery" | "library" | "personal" | "studio" | "work";
export type DestinationAvailability =
  | Readonly<{ state: "pending"; verification: "unverified" }>
  | Readonly<{ state: "ready"; verification: "observed"; evidence: string }>
  | Readonly<{ state: "unavailable"; verification: "observed"; evidence: string }>;

export type DestinationEntry = Readonly<{
  id: string;
  label: string;
  category: DestinationCategory;
  owner: DestinationOwner;
  parentId?: string;
  route: ShellRoute;
  workspaceMode: WorkspaceFrameMode;
  availability: DestinationAvailability;
}>;

export const DESTINATION_GROUP_COUNTS: Readonly<Record<DestinationCategory, number>> = Object.freeze({
  Chat: 8, Work: 34, Create: 43, Library: 25, Personal: 47,
  Inbox: 2, Apps: 13, Settings: 38, "Getting started": 4,
});

const OWNERS: ReadonlySet<string> = new Set<DestinationOwner>([
  "activity", "code", "communications", "conversation", "delivery",
  "discovery", "library", "personal", "studio", "work",
]);
type Definition = readonly [
  id: string, label: string, category: DestinationCategory, owner: DestinationOwner,
  shellDestination: ShellDestination, workspaceMode: WorkspaceFrameMode,
  parentId: string | null, availability: "pending",
];

const DEFINITIONS = [
  ["chat", "New conversation", "Chat", "conversation", "chat", "compact", null, "pending"],
  ["chat/history", "Conversation history", "Chat", "conversation", "chat", "compact", "chat", "pending"],
  ["chat/session", "Conversation", "Chat", "conversation", "chat", "compact", "chat", "pending"],
  ["chat/map", "Session map", "Chat", "conversation", "chat", "compact", "chat", "pending"],
  ["chat/import", "Import conversations", "Chat", "conversation", "chat", "compact", "chat", "pending"],
  ["dashboard", "Today", "Work", "activity", "activity", "full", null, "pending"],
  ["mission-control", "Live activity", "Work", "activity", "activity", "full", null, "pending"],
  ["inbox", "Needs you", "Inbox", "activity", "activity", "full", null, "pending"],
  ["notifications", "Notifications", "Inbox", "activity", "activity", "full", null, "pending"],
  ["apps", "App library", "Apps", "discovery", "apps", "full", null, "pending"],
  ["apps/install", "Install an app", "Apps", "discovery", "apps", "full", "apps", "pending"],
  ["app", "App workspace", "Apps", "discovery", "apps", "full", null, "pending"],
  ["discover", "Discover", "Apps", "discovery", "apps", "full", null, "pending"],
  ["connections", "Connections", "Apps", "discovery", "apps", "full", null, "pending"],
  ["capabilities/platform/providers", "Providers", "Apps", "discovery", "apps", "full", null, "pending"],
  ["capabilities/platform/integrations", "Integrations", "Apps", "discovery", "apps", "full", null, "pending"],
  ["capabilities/platform/operations", "Operations", "Apps", "discovery", "apps", "full", null, "pending"],
  ["capabilities/platform/evidence", "Evidence", "Apps", "discovery", "apps", "full", null, "pending"],
  ["capabilities/platform/network", "Network", "Apps", "discovery", "apps", "full", null, "pending"],
  ["capabilities/platform/sharing", "Sharing", "Apps", "discovery", "apps", "full", null, "pending"],
  ["capabilities/platform/migration", "Archive migration", "Apps", "discovery", "apps", "full", null, "pending"],
  ["capabilities/platform/api", "API catalog", "Apps", "discovery", "apps", "full", null, "pending"],
  ["settings/account", "Account", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/design", "Design", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/chat", "Chat", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/providers", "Providers", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/models", "Models", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/search", "Search", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/prompts", "Prompts", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/memory", "Memory", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/evals", "Evaluations", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/agent", "Agent defaults", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/voice", "Speech & Transcription", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/apps", "Apps", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/inbox", "Inbox", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/documents", "Documents", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/notifications", "Notifications", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/security", "Security", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/secrets", "Secrets", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/devices", "Devices", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/sender-trust", "Sender trust", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/guardrails", "Guardrails", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/external-access", "External access", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/audit", "Audit log", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/doctor", "Doctor", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/diagnostics", "Diagnostics", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/tool-output", "Tool output", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/feedback", "AI feedback", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/usage", "Usage", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/routing", "Routing & Efficiency", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/legibility", "Legibility", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/ambient", "Ambient surfaces", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/companion", "Companion apps", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/sources", "Watched sources", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/packs", "Packs", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/archive", "Archive", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/portability", "Import / Export", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/durability", "Backups", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/updates", "Updates", "Settings", "discovery", "apps", "full", null, "pending"],
  ["settings/runtime-config", "Runtime configuration", "Settings", "discovery", "apps", "full", null, "pending"],
  ["learning", "Learning", "Personal", "personal", "ideas", "full", null, "pending"],
  ["companion", "Companion", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/knowledge/ideas", "Ideas", "Library", "personal", "ideas", "full", null, "pending"],
  ["capabilities/knowledge/journals", "Journal", "Library", "personal", "ideas", "full", null, "pending"],
  ["capabilities/identity/autobiography", "Autobiography", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/identity/twin", "Human profile", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/identity/goals", "Life goals", "Personal", "personal", "goals", "full", null, "pending"],
  ["capabilities/identity/goal-plans", "Goal plans", "Personal", "personal", "goals", "full", null, "pending"],
  ["capabilities/identity/progress", "Progress", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/identity/fidelity", "Profile checks", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/identity/continuity", "Agent continuity", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/identity/bundles", "Identity bundles", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/identity/recipes", "Tool recipes", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/identity/guarded-recipes", "Guarded recipes", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/identity/lifecycle", "Agent lifecycle", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/overview", "Overview", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/measurements", "Readings", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/labs", "Laboratory", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/body-composition", "Body", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/eyes", "Vision", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/epigenetic", "Biological age", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/lifestyle", "Lifestyle", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/consumption", "Consumption", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/interventions", "Interventions", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/cognition", "Practice", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/memory", "Memory", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/life", "Life calendar", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/genome", "Genome", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/import", "Import", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/exports", "Exports", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/shared", "Shared health", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/privacy", "Privacy", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/wellbeing/organizations", "Organizations", "Personal", "personal", "ideas", "full", null, "pending"],
  ["capabilities/communications/people", "People", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/inbox", "Inbox", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/calendar", "Calendar", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/beeper", "Beeper", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/outbound", "Outbound email", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/desktop", "Desktop imports", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/imports", "People imports", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/threads", "Threads", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/teams", "Teams", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/telegram", "Telegram", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/social", "Social accounts", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/x", "X reading", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/stacker", "Stacker News", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/signal", "Signal archive", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/lifecycle", "Lifecycle", "Personal", "communications", "apps", "full", null, "pending"],
  ["capabilities/communications/timeline", "Timeline", "Personal", "communications", "apps", "full", null, "pending"],
  ["agents", "Agents", "Work", "work", "activity", "full", null, "pending"],
  ["agents/new", "Create an agent", "Work", "work", "activity", "full", "agents", "pending"],
  ["agents/inspect", "Agent controls", "Work", "work", "activity", "full", "agents", "pending"],
  ["tasks", "Tasks", "Work", "work", "activity", "full", null, "pending"],
  ["tasks/new", "Create a task", "Work", "work", "activity", "full", "tasks", "pending"],
  ["tasks/graph", "Dependencies", "Work", "work", "activity", "full", "tasks", "pending"],
  ["workflows", "Workflows", "Work", "work", "activity", "full", null, "pending"],
  ["workflows/definition", "Workflow builder", "Work", "work", "activity", "full", "workflows", "pending"],
  ["workflows/run", "Workflow run", "Work", "work", "activity", "full", "workflows", "pending"],
  ["loops", "Ongoing work", "Work", "work", "activity", "full", null, "pending"],
  ["loops/new", "Start ongoing work", "Work", "work", "activity", "full", "loops", "pending"],
  ["loops/run", "Loop controls", "Work", "work", "activity", "full", "loops", "pending"],
  ["triggers", "Schedules & triggers", "Work", "work", "activity", "full", null, "pending"],
  ["triggers/new", "Create an automation", "Work", "work", "activity", "full", "triggers", "pending"],
  ["experiments", "Experiments", "Work", "work", "activity", "full", null, "pending"],
  ["experiments/replay", "Run replay", "Work", "work", "activity", "full", "experiments", "pending"],
  ["skills", "Skills", "Work", "work", "activity", "full", null, "pending"],
  ["tools", "Tools", "Work", "work", "activity", "full", null, "pending"],
  ["rooms", "Rooms", "Chat", "work", "activity", "compact", null, "pending"],
  ["rooms/new", "Create a room", "Chat", "work", "activity", "compact", "rooms", "pending"],
  ["rooms/conversation", "Room conversation", "Chat", "work", "activity", "compact", "rooms", "pending"],
  ["projects", "Projects", "Work", "code", "apps", "full", null, "pending"],
  ["projects/detail", "Project workspace", "Work", "code", "apps", "full", "projects", "pending"],
  ["code", "Code projects", "Work", "code", "apps", "full", null, "pending"],
  ["code/workspace", "Code workspace", "Work", "code", "apps", "full", "code", "pending"],
  ["files", "Files", "Library", "code", "apps", "full", null, "pending"],
  ["artifacts", "Deliverables", "Library", "code", "apps", "full", null, "pending"],
  ["artifacts/editor", "Artifact editor", "Library", "code", "apps", "full", "artifacts", "pending"],
  ["terminal", "Terminal", "Work", "code", "apps", "full", null, "pending"],
  ["capabilities/workspace/context", "Saved context", "Work", "code", "apps", "full", null, "pending"],
  ["capabilities/workspace/processes", "Processes", "Work", "code", "apps", "full", null, "pending"],
  ["capabilities/workspace/ports", "Ports", "Work", "code", "apps", "full", null, "pending"],
  ["capabilities/workspace/projects", "Projects", "Work", "code", "apps", "full", null, "pending"],
  ["capabilities/workspace/git", "Git", "Work", "code", "apps", "full", null, "pending"],
  ["capabilities/workspace/desktop", "Desktop", "Work", "code", "apps", "full", null, "pending"],
  ["capabilities/workspace/native", "Native terminals", "Work", "code", "apps", "full", null, "pending"],
  ["capabilities/workspace/provider", "Provider CLI", "Work", "code", "apps", "full", null, "pending"],
  ["capabilities/workspace/storage", "Storage", "Work", "code", "apps", "full", null, "pending"],
  ["knowledge", "Knowledge", "Library", "library", "apps", "full", null, "pending"],
  ["knowledge/item", "Knowledge reader", "Library", "library", "apps", "full", "knowledge", "pending"],
  ["knowledge/new", "Add knowledge", "Library", "library", "apps", "full", "knowledge", "pending"],
  ["knowledge/graph", "Knowledge graph", "Library", "library", "apps", "full", "knowledge", "pending"],
  ["knowledge/sources", "Sources", "Library", "library", "apps", "full", "knowledge", "pending"],
  ["knowledge/sources/new", "Add a source", "Library", "library", "apps", "full", "knowledge/sources", "pending"],
  ["knowledge/reports", "Research reports", "Library", "library", "apps", "full", "knowledge", "pending"],
  ["prompts", "Prompts", "Library", "library", "apps", "full", null, "pending"],
  ["prompts/new", "Write a prompt", "Library", "library", "apps", "full", "prompts", "pending"],
  ["prompts/view", "Prompt editor", "Library", "library", "apps", "full", "prompts", "pending"],
  ["capabilities/knowledge/overview", "Overview", "Library", "library", "apps", "full", null, "pending"],
  ["capabilities/knowledge/capture", "Capture inbox", "Library", "library", "apps", "full", null, "pending"],
  ["capabilities/knowledge/topics", "Topics", "Library", "library", "apps", "full", null, "pending"],
  ["capabilities/knowledge/reviews", "Reviews", "Library", "library", "apps", "full", null, "pending"],
  ["capabilities/knowledge/rsvp", "Reader", "Library", "library", "apps", "full", null, "pending"],
  ["capabilities/knowledge/videos", "Videos", "Library", "library", "apps", "full", null, "pending"],
  ["capabilities/knowledge/links", "Saved links", "Library", "library", "apps", "full", null, "pending"],
  ["capabilities/knowledge/archives", "Conversation imports", "Library", "library", "apps", "full", null, "pending"],
  ["capabilities/knowledge/vaults", "Connected folders", "Library", "library", "apps", "full", null, "pending"],
  ["capabilities/knowledge/types", "Record types", "Library", "library", "apps", "full", null, "pending"],
  ["design", "Design studio", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/sketches", "Image sketches", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/images", "Image generation", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/videos", "Video generation", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/animations", "Code animation", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/sprites", "Sprite production", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/episodes", "Continuous episodes", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/timelines", "Video timeline", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/cleanup", "Image cleanup", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/datasets", "LoRA datasets", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/downloads", "Source downloader", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/library", "Media library", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/jobs", "Media jobs", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/media/readiness", "Media readiness", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/ingredients", "Ingredients", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/boards", "Moodboards", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/universes", "Universes", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/authors", "Authors", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/works", "Writing", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/stories", "Stories", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/series", "Series", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/production", "Production", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/direction", "Direction", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/commissions", "Commissions", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/creative/exports", "Exports", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/music/repertoire", "Songbook", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/music/catalog", "Music catalog", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/music/listening", "Listening", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/music/decks", "Music cards", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/music/rounds", "Music rounds", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/music/midi", "MIDI studio", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/music/videos", "Music videos", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/music/models3d", "3D models", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/music/assemblies", "Assemblies", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/experience/stories", "Story loom", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/experience/games", "Game assets", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/experience/world", "World canvas", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/experience/foundations", "Foundations", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/experience/travel", "World travel", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/experience/voice", "Voice & avatar", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/experience/calls", "Calls", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/experience/moltworld", "Moltworld", "Create", "studio", "apps", "full", null, "pending"],
  ["capabilities/experience/moltbook", "Moltbook", "Create", "studio", "apps", "full", null, "pending"],
  ["onboarding/welcome", "Welcome", "Getting started", "delivery", "apps", "full", null, "pending"],
  ["onboarding/connect", "Connect Gideon", "Getting started", "delivery", "apps", "full", null, "pending"],
  ["onboarding/model", "Choose your first model", "Getting started", "delivery", "apps", "full", null, "pending"],
  ["onboarding/preferences", "Make it yours", "Getting started", "delivery", "apps", "full", null, "pending"],
] as const satisfies readonly Definition[];

export const DESTINATIONS: readonly DestinationEntry[] = Object.freeze(DEFINITIONS.map(([
  id, label, category, owner, shellDestination, workspaceMode, parentId, availability,
]) => Object.freeze({
  id, label, category, owner,
  ...(parentId ? { parentId } : {}),
  route: createShellRoute(shellDestination, {
    view: workspaceMode === "full" ? "workspace" : "list",
    placement: { id },
  }),
  workspaceMode,
  availability: Object.freeze({ state: availability, verification: "unverified" }),
})));

export function validateDestinations(entries: readonly DestinationEntry[], expectedIds: readonly string[]): string[] {
  const errors: string[] = [];
  const expected = new Set(expectedIds);
  const seen = new Set<string>();
  const byId = new Map(entries.map(entry => [entry.id, entry]));
  if (expectedIds.length !== expected.size) errors.push("Expected IDs contain duplicates");
  if (entries.length !== 214) errors.push(`Expected 214 destinations, got ${entries.length}`);
  for (const entry of entries) {
    if (seen.has(entry.id)) errors.push(`Duplicate ID: ${entry.id}`);
    seen.add(entry.id);
    if (!expected.has(entry.id)) errors.push(`Extra ID: ${entry.id}`);
    if (!entry.label.trim()) errors.push(`Missing label: ${entry.id}`);
    if (!(entry.category in DESTINATION_GROUP_COUNTS)) errors.push(`Missing category: ${entry.id}`);
    if (!OWNERS.has(entry.owner)) errors.push(`Missing owner: ${entry.id}`);
    if (entry.workspaceMode !== "compact" && entry.workspaceMode !== "full") errors.push(`Missing workspace mode: ${entry.id}`);
    try {
      if (!entry.route || entry.route.kind !== "route" || entry.route.placement?.id !== entry.id
        || parseShellRoute(serializeShellRoute(entry.route)).kind !== "route") errors.push(`Broken route: ${entry.id}`);
    } catch { errors.push(`Broken route: ${entry.id}`); }
    if (!entry.availability || !["pending", "ready", "unavailable"].includes(entry.availability.state)
      || (entry.availability.state === "pending" && entry.availability.verification !== "unverified")
      || (entry.availability.state !== "pending" && (entry.availability.verification !== "observed" || !entry.availability.evidence?.trim()))) {
      errors.push(`Missing availability or verification: ${entry.id}`);
    }
    const parts = entry.id.split("/");
    const nearest = parts.slice(1).map((_, index) => parts.slice(0, index + 1).join("/"))
      .filter(parent => byId.has(parent)).at(-1);
    if (entry.parentId !== nearest || (entry.parentId && !byId.has(entry.parentId))) errors.push(`Broken parent: ${entry.id}`);
  }
  for (const id of expected) if (!seen.has(id)) errors.push(`Missing ID: ${id}`);
  for (const [category, count] of Object.entries(DESTINATION_GROUP_COUNTS)) {
    const actual = entries.filter(entry => entry.category === category).length;
    if (actual !== count) errors.push(`Wrong ${category} count: ${actual}`);
  }
  return errors;
}
