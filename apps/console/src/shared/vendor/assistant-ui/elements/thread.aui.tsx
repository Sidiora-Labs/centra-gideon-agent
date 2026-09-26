"use client";

import { MessagePrimitive, ThreadPrimitive, useAuiState, type AssistantState } from "@assistant-ui/react";
import type { ComponentType, ReactNode, Ref } from "react";

export type ThreadComponents = {
  UserMessage?: ComponentType;
  AssistantMessage?: ComponentType;
  Welcome?: ComponentType;
  ToolFallback?: ComponentType;
  ToolGroup?: ComponentType;
  ReasoningGroup?: ComponentType;
  TaskGroup?: ComponentType;
};

export type ThreadTranscriptProps = {
  components?: ThreadComponents;
  viewportRef?: Ref<HTMLDivElement>;
  beforeMessages?: ReactNode;
  afterMessages?: ReactNode;
  afterViewport?: ReactNode;
};

export function useMessage<T>(selector: (message: AssistantState["message"]) => T): T {
  return useAuiState((state) => selector(state.message));
}

function DefaultUserMessage() {
  return (
    <MessagePrimitive.Root data-slot="aui_user-message-root" data-role="user">
      <MessagePrimitive.Parts />
    </MessagePrimitive.Root>
  );
}

function DefaultAssistantMessage() {
  return (
    <MessagePrimitive.Root data-slot="aui_assistant-message-root" data-role="assistant">
      <MessagePrimitive.Parts />
    </MessagePrimitive.Root>
  );
}

function ThreadMessage({ components }: { components: ThreadComponents }) {
  const role = useAuiState((state) => state.message.role);
  const UserMessage = components.UserMessage ?? DefaultUserMessage;
  const AssistantMessage = components.AssistantMessage ?? DefaultAssistantMessage;
  return role === "user" ? <UserMessage /> : <AssistantMessage />;
}

export function ThreadMessages({ components = {} }: { components?: ThreadComponents }) {
  return (
    <ThreadPrimitive.Messages>
      {() => <ThreadMessage components={components} />}
    </ThreadPrimitive.Messages>
  );
}

export function ThreadTranscript({
  components = {},
  viewportRef,
  beforeMessages,
  afterMessages,
  afterViewport,
}: ThreadTranscriptProps) {
  return (
    <ThreadPrimitive.Root className="aui-root aui-thread-root flex h-full flex-col">
      <ThreadPrimitive.Viewport
        ref={viewportRef}
        turnAnchor="top"
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-auto scroll-smooth"
      >
        {beforeMessages}
        <div data-slot="aui_message-group" className="flex flex-col gap-y-6 empty:hidden">
          <ThreadMessages components={components} />
        </div>
        {afterMessages}
      </ThreadPrimitive.Viewport>
      {afterViewport}
    </ThreadPrimitive.Root>
  );
}
