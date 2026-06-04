# Gideon Provider Reference

The extension-provider taxonomy (the capability types an app can contribute) and the providers currently registered in this build.

## Provider types

- `action`
- `agent`
- `channel`
- `inbox`
- `knowledge`
- `memory`
- `model`
- `notification`
- `prompt`
- `search`
- `skills`
- `task`
- `tool`
- `workflow`

## Registered providers

- **bash-action** — type `action` / `` (enabled); capabilities: execute, blocking
- **create-task-action** — type `action` / `` (enabled); capabilities: execute
- **invoke-agent-action** — type `action` / `` (enabled); capabilities: execute
- **notify-action** — type `action` / `` (enabled); capabilities: execute
- **run-prompt-action** — type `action` / `` (enabled); capabilities: execute
- **run-script-action** — type `action` / `` (enabled); capabilities: execute
- **run-workflow-action** — type `action` / `` (enabled); capabilities: execute
- **send-message-action** — type `action` / `` (enabled); capabilities: execute
- **native-agents** — type `agent` / `` (enabled); capabilities: crud, acp
- **filesystem-inbox** — type `inbox` / `` (enabled); capabilities: approvals, inputs
- **native-knowledge** — type `knowledge` / `` (enabled); capabilities: bookmarks, documents, search
- **native-vector-memory** — type `memory` / `` (enabled); capabilities: semantic_search, episodic, preferences
- **native-prompts** — type `prompt` / `` (enabled); capabilities: list, read, write, render
- **native-skills** — type `skills` / `` (enabled); capabilities: crud, triggers, auto_generation
- **native-tasks** — type `task` / `` (enabled); capabilities: crud, comments, labels, dependencies
- **gideon-artifacts** — type `tool` / `` (enabled); capabilities: artifacts
- **gideon-code-map** — type `tool` / `` (enabled); capabilities: code_map
- **gideon-inbox-tools** — type `tool` / `` (enabled); capabilities: inbox
- **gideon-knowledge-tools** — type `tool` / `` (enabled); capabilities: knowledge
- **gideon-memory** — type `tool` / `` (enabled); capabilities: memory
- **gideon-project-tools** — type `tool` / `` (enabled); capabilities: projects
- **gideon-schedule-tools** — type `tool` / `` (enabled); capabilities: schedule_management
- **gideon-subagents** — type `tool` / `` (enabled); capabilities: subagents
- **gideon-tasks-tools** — type `tool` / `` (enabled); capabilities: task
- **gideon-tools** — type `tool` / `` (enabled); capabilities: skills, notification, system
- **gideon-ui-docs** — type `tool` / `` (enabled); capabilities: ui_docs
- **gideon-workflows** — type `tool` / `` (enabled); capabilities: workflows
- **native-workflows** — type `workflow` / `` (enabled); capabilities: crud, scoped, semantic-match
