# Gideon assistant implementation

Implement a chat-first assistant interface with five primary destinations: Chat, Activity, Ideas, Goals and Apps. Preserve existing Gideon capabilities, durable records and action permissions. Open complex tools in dedicated workspaces with a direct return to the active conversation.

The following implementation lists describe planned work. Unchecked items are not implementation or release-completion claims.

- [ ] [Assistant shell and navigation](shell.md)
- [ ] [Conversation and rich results](conversation.md)
- [ ] [Activity and review](activity.md)
- [ ] [Apps and settings discovery](discovery.md)
- [ ] [Ideas, goals and personal context](personal.md)
- [ ] [Communications and calendar](communications.md)
- [ ] [Tasks, workflows and collaboration](work.md)
- [ ] [Code, files and computer workspaces](code.md)
- [ ] [Persistent browser sessions](browser.md)
- [ ] [Research and knowledge](library.md)
- [ ] [Creative workspaces](studio.md)
- [ ] [Application delivery and compatibility](delivery.md)

[Destination implementation checklist](destinations.md) covers the complete route inventory.

## Implementation order

1. Establish the application entry, authentication contract, visual shell and route boundaries.
2. Connect a real conversation and representative complex workspace, then complete conversation actions and recovery.
3. Add Activity, Apps discovery and linked utility journeys.
4. Integrate work, code, personal, communications, knowledge and creative workspaces.
5. Complete persistent browser control and remaining workspace lifecycle contracts.
6. Verify distribution, deep links, accessibility, responsive behavior and upgrade continuity.

## Completion criteria

- [ ] Every existing destination has an explicit route or documented replacement with its essential actions preserved.
- [ ] Chat, work status, approvals and results resolve to canonical Gideon records with stable identifiers.
- [ ] Named applications are searchable and directly launchable; large editors and canvases use appropriate workspace geometry.
- [ ] Empty, unavailable, loading, error and recovery states are actionable and distinguishable.
- [ ] Keyboard, touch and responsive journeys preserve focus, drafts and navigation context.
- [ ] Completion evidence covers real user actions and persisted outcomes, including failures and reconnection.
