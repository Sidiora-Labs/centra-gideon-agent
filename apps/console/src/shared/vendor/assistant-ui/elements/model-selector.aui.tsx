"use client";

import { useEffect } from "react";
import { useAui } from "@assistant-ui/react";
import { ModelSelectorRoot, ModelSelectorTrigger, useModelSelectorContext, type ModelSelectorRootProps } from "./model-selector";

export { ModelSelectorRoot, ModelSelectorTrigger, useModelSelectorContext } from "./model-selector";
export type { ModelOption, ModelSelectorRootProps } from "./model-selector";

export function ModelSelectorModelContext() {
  const { value } = useModelSelectorContext();
  const api = useAui();
  useEffect(() => {
    if (value === undefined || value === "Auto") return;
    return api.modelContext.register({
      getModelContext: () => ({ config: { modelName: value } }),
    });
  }, [api, value]);
  return null;
}

export function ModelSelector(props: Omit<ModelSelectorRootProps, "children">) {
  return (
    <ModelSelectorRoot {...props}>
      <ModelSelectorModelContext />
      <ModelSelectorTrigger />
    </ModelSelectorRoot>
  );
}
