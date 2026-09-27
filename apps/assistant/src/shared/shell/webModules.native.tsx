import type { ComponentType } from "react";
import type { OwnerScope } from "../auth.web";
import type { ShellRoute, ShellReturnContext } from "./shellRoutes";

export type ModuleProps = {
  route: ShellRoute;
  scope: OwnerScope;
  navigate: (route: ShellRoute) => void;
  returnTo?: ShellReturnContext;
  onReturn: () => void;
};

export type ModuleDefinition = {
  id: string;
  mode: "compact" | "full";
  matches: (route: ShellRoute) => boolean;
  resolve: (scope: OwnerScope, route: ShellRoute) => Promise<"available" | "missing" | "denied" | "unavailable">;
  load: () => Promise<{ default: ComponentType<ModuleProps> }>;
};

export const moduleDefinitions: readonly ModuleDefinition[] = Object.freeze([]);

export function moduleForRoute(_route: ShellRoute): ModuleDefinition | undefined {
  return undefined;
}

export async function resolveModuleRoute(_scope: OwnerScope, route: ShellRoute): Promise<"available" | "missing" | "denied" | "unavailable" | undefined> {
  return route.placement ? "unavailable" : undefined;
}

export function TrustedModuleContent(_props: ModuleProps & { definition: ModuleDefinition }) {
  return null;
}
