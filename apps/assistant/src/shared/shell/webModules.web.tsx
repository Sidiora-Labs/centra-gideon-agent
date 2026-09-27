import type { ComponentType } from "react";
import type { OwnerScope } from "../auth.web";
import type { ShellRoute, ShellReturnContext } from "./shellRoutes";
import type { RouteAvailability } from "./routeState.web";
import type { WorkspaceFrameMode } from "./WorkspaceFrame.web";

export type ModuleProps = {
  route: ShellRoute;
  scope: OwnerScope;
  navigate: (route: ShellRoute) => void;
  returnTo?: ShellReturnContext;
  onReturn: () => void;
};

export type ModuleDefinition = {
  id: string;
  mode: WorkspaceFrameMode;
  matches: (route: ShellRoute) => boolean;
  resolve: (scope: OwnerScope, route: ShellRoute) => Promise<RouteAvailability>;
  load: () => Promise<{ default: ComponentType<ModuleProps> }>;
};
