import { createShellRoute, type ShellRoute } from "../../shared/shell/shellRoutes";
import { DESTINATIONS, type DestinationCategory } from "./destinations";

export type Miniapp = Readonly<{
  id: string;
  name: string;
  description: string;
  category: DestinationCategory;
  destinationId: string;
  firstAction: string;
  route: ShellRoute;
}>;

type MiniappDefinition = readonly [
  id: string,
  name: string,
  description: string,
  category: DestinationCategory,
  destinationId: string,
  firstAction: string,
  subview?: string,
];

const DEFINITIONS = [
  ["research", "Research", "Find reports and continue source-backed investigations.", "Library", "knowledge/reports", "Open research reports"],
  ["slides", "Slides", "Turn an outline into a presentation, then edit and export the deck.", "Create", "design", "Open slide production", "/slides"],
  ["studio", "Studio", "Create and review images in the media workspace.", "Create", "capabilities/media/images", "Open image creation"],
  ["writer", "Writer", "Develop creative works and continue from saved revisions.", "Create", "capabilities/creative/works", "Open writing"],
  ["music", "Music", "Explore repertoire and continue music work.", "Create", "capabilities/music/repertoire", "Open repertoire"],
  ["worlds", "Worlds", "Continue stories and shape connected worlds.", "Create", "capabilities/experience/stories", "Open stories"],
  ["knowledge", "Knowledge", "Browse saved knowledge and open source-linked records.", "Library", "knowledge", "Browse knowledge"],
  ["journal", "Journal", "Read and write the current owner’s dated journal.", "Library", "capabilities/knowledge/journals", "Open journal"],
  ["health", "Health", "Review private wellbeing records in the Health workspace.", "Personal", "capabilities/wellbeing/overview", "Open health overview"],
  ["people", "People", "Find people and continue from their canonical contact records.", "Personal", "capabilities/communications/people", "Browse people"],
  ["compass", "Compass", "Review human goals separately from tasks and automation.", "Personal", "capabilities/identity/goals", "Review your goals"],
  ["automations", "Automations", "Inspect workflows and continue automation work.", "Work", "workflows", "Open workflows"],
  ["code", "Code", "Open code projects and continue in a selected workspace.", "Work", "code", "Open code projects"],
  ["agents", "Agents", "Review agents and continue their operational work.", "Work", "agents", "Open agents"],
  ["lab", "Lab", "Inspect experiments and their recorded results.", "Work", "experiments", "Open experiments"],
  ["workspace", "Workspace", "Browse working environments and open a project workspace.", "Work", "projects", "Open working environments"],
  ["connections", "Connections", "Review personal account connections and their current state.", "Apps", "connections", "Manage connections"],
] as const satisfies readonly MiniappDefinition[];

function routeFor(definition: MiniappDefinition): ShellRoute {
  const destination = DESTINATIONS.find(entry => entry.id === definition[4]);
  if (!destination) throw new Error(`Miniapp target is missing from the destination registry: ${definition[4]}`);
  if (!definition[6]) return destination.route;
  if (definition[4] !== "design" || definition[6] !== "/slides") {
    throw new Error(`Unsupported miniapp subview for ${definition[0]}`);
  }
  return createShellRoute(destination.route.destination, {
    view: destination.route.view,
    placement: { id: destination.id, subview: definition[6] },
  });
}

export const MINIAPPS: readonly Miniapp[] = Object.freeze(DEFINITIONS.map(definition => Object.freeze({
  id: definition[0],
  name: definition[1],
  description: definition[2],
  category: definition[3],
  destinationId: definition[4],
  firstAction: definition[5],
  route: routeFor(definition),
})));

export type MiniappCheckState = "pending" | "checking" | "ready" | "missing" | "denied" | "unavailable";

export function miniappStateMessage(state: MiniappCheckState, name: string): string {
  switch (state) {
    case "pending": return "Owner availability has not been checked yet.";
    case "checking": return `Checking ${name} with its owning workspace…`;
    case "ready": return `${name} workspace is available for this account.`;
    case "missing": return `${name} first-action workspace is not available yet.`;
    case "denied": return `This account cannot open ${name}.`;
    case "unavailable": return `${name} could not be checked right now.`;
  }
}
