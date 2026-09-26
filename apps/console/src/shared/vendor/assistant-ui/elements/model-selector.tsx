"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

export type ModelOption = {
  id: string;
  name: string;
  description?: string;
  disabled?: boolean;
};

export type ModelSelectorRootProps = {
  models: readonly ModelOption[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  children: ReactNode;
};

type Selection = {
  models: readonly ModelOption[];
  value: string | undefined;
  select: (value: string) => void;
};

const ModelSelectorContext = createContext<Selection | null>(null);

export function useModelSelectorContext(): Selection {
  const selection = useContext(ModelSelectorContext);
  if (!selection) throw new Error("ModelSelector must be inside ModelSelectorRoot");
  return selection;
}

export function ModelSelectorRoot(props: ModelSelectorRootProps) {
  const { models, value, defaultValue, onValueChange, children } = props;
  const controlled = Object.prototype.hasOwnProperty.call(props, "value");
  const [internal, setInternal] = useState(defaultValue ?? models[0]?.id);
  const current = controlled ? value : internal;
  const select = (next: string) => {
    if (!controlled) setInternal(next);
    onValueChange?.(next);
  };
  return (
    <ModelSelectorContext.Provider value={{ models, value: current, select }}>
      {children}
    </ModelSelectorContext.Provider>
  );
}

export function ModelSelectorTrigger() {
  const { models, value, select } = useModelSelectorContext();
  return (
    <select
      data-slot="model-selector-trigger"
      aria-label="Model"
      value={value ?? ""}
      onChange={event => select(event.target.value)}
    >
      {value === undefined && <option value="">Select model</option>}
      {models.map(model => (
        <option key={model.id} value={model.id} disabled={model.disabled}>
          {model.name}
        </option>
      ))}
    </select>
  );
}
