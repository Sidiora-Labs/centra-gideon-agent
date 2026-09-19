"""Project inventory and work-board response assembly."""

from typing import Any

from gideon.automation.workflows import containers


class LinkedProjectInventory:
    def __init__(self, project_id, app):
        self.project_id = project_id
        self.app = app
        self.sections: dict[str, list[Any]] = {
            name: [] for name in ("loops", "code", "artifacts", "chats", "knowledge")
        }

    def collect(self):
        for collect in (self.loops, self.artifacts, self.knowledge, self.chats):
            try:
                collect()
            except Exception:
                pass
        return self.sections

    def loops(self):
        from gideon.automation.loop import store

        for record in store.list_all():
            if self.project_id not in (record.project_id, record.tasks_project_id):
                continue
            section = "code" if record.kind == "code" else "loops"
            self.sections[section].append(
                {
                    "id": record.id,
                    "name": record.name or record.task[:60],
                    "status": record.status,
                    "kind": record.kind,
                    "error_message": record.error_message or None,
                }
            )

    def artifacts(self):
        from gideon.workspace.artifacts.registry import get_provider

        provider = get_provider()
        if provider is None:
            return
        tags = {
            f"loop:{record['id']}"
            for section in ("loops", "code")
            for record in self.sections[section]
        }
        seen = set()
        for record in provider.list():
            belongs = record.project_id == self.project_id or bool(
                tags.intersection(record.tags)
            )
            if belongs and record.slug not in seen:
                seen.add(record.slug)
                self.sections["artifacts"].append(
                    {
                        "slug": record.slug,
                        "name": record.name,
                        "kind": record.kind,
                    }
                )

    def knowledge(self):
        from gideon.cognition.knowledge import project_scope
        from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path

        self.sections["knowledge"] = project_scope.project_items(
            KnowledgeStore(db_path=str(knowledge_db_path())),
            project_id=self.project_id,
            limit=25,
        )

    def chats(self):
        for session in self.app["state"]._sessions.values():
            if getattr(session, "project_id", "") == self.project_id and not str(
                getattr(session, "_app", "") or ""
            ):
                self.sections["chats"].append(
                    {
                        "key": session.key,
                        "title": getattr(session, "title", "") or session.key,
                        "running": bool(getattr(session, "running", False)),
                    }
                )


class BoardProjection:
    @staticmethod
    def decode(payload):
        claim_data = payload.get("claim")
        claim = None
        if isinstance(claim_data, dict):
            claim = containers.Claim(
                holder=str(claim_data.get("holder", "") or ""),
                expires_at=float(claim_data.get("expires_at") or 0.0),
                taken_at=float(claim_data.get("taken_at") or 0.0),
                renewals=int(claim_data.get("renewals") or 0),
            )
        try:
            state = containers.BoardState(str(payload.get("state", "") or ""))
        except ValueError:
            state = containers.BoardState.WORKING
        return containers.BoardRow(
            run_id=str(payload.get("run_id") or ""),
            title=str(payload.get("title") or ""),
            origin=str(payload.get("origin") or ""),
            project_id=str(payload.get("project_id") or ""),
            collapsed=bool(payload.get("collapsed", False)),
            attention=bool(payload.get("attention", False)),
            resumable=bool(payload.get("resumable", False)),
            state=state,
            claim=claim,
        )

    @staticmethod
    def loop(record, project_id, states):
        state = states.get(str(record.status), containers.BoardState.WORKING)
        return containers.BoardRow(
            run_id=record.id,
            title=record.name or (record.task or "")[:60] or "(unnamed loop)",
            state=state,
            origin="manual",
            project_id=project_id,
            resumable=state is containers.BoardState.SUSPENDED,
            attention=state is containers.BoardState.NEEDS_INPUT,
        ).to_dict()

    @staticmethod
    def task(record, project_id, states):
        status = getattr(getattr(record, "status", None), "value", "") or ""
        state = states.get(status, containers.BoardState.QUEUED)
        return containers.BoardRow(
            run_id=getattr(record, "id", "") or "",
            title=getattr(record, "title", "") or "(untitled task)",
            state=state,
            origin="task",
            project_id=project_id,
            attention=state is containers.BoardState.NEEDS_INPUT,
        ).to_dict()


class ProjectBoard:
    def __init__(self, project, now):
        self.project = project
        self.now = now
        self.tasks = []
        self.task_error = None

    async def prefetch(self):
        try:
            from gideon.engine.tasks import registry

            self.tasks, _ = await registry.list_all_tasks(
                project=self.project.name, limit=10_000
            )
        except Exception as exc:
            self.task_error = exc

    def task_rows(self, project_tasks):
        if self.task_error is not None:
            raise self.task_error
        return project_tasks(self.tasks, self.project.id)

    def response(self, run_rows, loop_rows, task_rows, decode_row):
        sections, completeness = containers.collect_sections(
            {
                "runs": lambda: run_rows(self.project.id, self.now),
                "loops": lambda: loop_rows(self.project.id),
                "tasks": lambda: self.task_rows(task_rows),
            },
            now=self.now,
        )
        rows = [
            decode_row(item)
            for section in sections
            if section.get("status") == "ok"
            for item in section.get("items") or []
        ]
        return {
            "board": containers.group_board(rows),
            "sections": sections,
            "completeness": completeness.value,
            "attention": containers.attention_count(rows),
            "loadedAt": self.now,
        }
