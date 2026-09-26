"use client";

import { ThreadListItemPrimitive, ThreadListPrimitive } from "@assistant-ui/react";
import type { ReactNode } from "react";

export function ThreadListItem() {
  return (
    <ThreadListItemPrimitive.Root data-slot="aui_thread-list-item">
      <ThreadListItemPrimitive.Trigger data-slot="aui_thread-list-item-trigger">
        <ThreadListItemPrimitive.Title fallback="New Chat" />
      </ThreadListItemPrimitive.Trigger>
    </ThreadListItemPrimitive.Root>
  );
}

export function ThreadList() {
  return (
    <ThreadListPrimitive.Root data-slot="aui_thread-list-root">
      <ThreadListPrimitive.New data-slot="aui_thread-list-new">New Thread</ThreadListPrimitive.New>
      <ThreadListPrimitive.Items components={{ ThreadListItem }} />
    </ThreadListPrimitive.Root>
  );
}

export type ThreadListSidebarProps = {
  header?: ReactNode;
  footer?: ReactNode;
  children?: ReactNode;
};

export function ThreadListSidebar({ header, footer, children }: ThreadListSidebarProps) {
  return (
    <aside data-slot="aui_thread-list-sidebar" className="flex h-full flex-col">
      <header data-slot="aui_sidebar-header">
        {header === undefined ? <a href="https://assistant-ui.com" target="_blank" rel="noopener noreferrer">assistant-ui</a> : header}
      </header>
      <div data-slot="aui_sidebar-content" className="min-h-0 flex-1 overflow-y-auto">
        {children ?? <ThreadList />}
      </div>
      <footer data-slot="aui_sidebar-footer">
        {footer === undefined ? <a href="https://github.com/assistant-ui/assistant-ui" target="_blank" rel="noopener noreferrer">GitHub · View Source</a> : footer}
      </footer>
    </aside>
  );
}
