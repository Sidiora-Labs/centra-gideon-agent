"""Bounded, read-only storage attribution for runtime and canonical projects."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from gideon.engine.tasks.hierarchy import HierarchyStore


class StorageDiagnosis:
    def __init__(self, home, *, allowed_roots, hierarchy=None, entry_limit=10000):
        self.home = Path(home).resolve(strict=True)
        self.allowed_roots = [Path(value).resolve() for value in allowed_roots]
        self.hierarchy = hierarchy if hierarchy is not None else HierarchyStore()
        if type(entry_limit) is not int or entry_limit < 1 or entry_limit > 100000:
            raise ValueError("Entry limit must be between 1 and 100000")
        self.entry_limit = entry_limit

    def _project(self, project_id):
        if not isinstance(project_id, str) or not project_id.strip() or len(project_id) > 256:
            raise ValueError("Invalid project ID")
        project = self.hierarchy.get_project(project_id)
        if project is None or not project.workspace_dir:
            raise FileNotFoundError("Project not found")
        raw = Path(project.workspace_dir)
        if not raw.is_absolute() or raw.is_symlink():
            raise ValueError("Canonical project workspace is not a safe directory")
        workspace = raw.resolve(strict=True)
        if not workspace.is_dir() or workspace == Path(workspace.anchor):
            raise ValueError("Canonical project workspace is not a safe directory")
        if not any(workspace.is_relative_to(root) for root in self.allowed_roots):
            raise ValueError("Canonical project workspace is outside allowed roots")
        return project, workspace

    def _scan(self, root, entry_limit=None):
        root = Path(root)
        total_bytes = 0
        files = 0
        directories = 0
        skipped_symlinks = 0
        skipped_mounts = 0
        errors = []
        stack = [root]
        seen = set()
        root_device = root.stat(follow_symlinks=False).st_dev
        visited = 0
        limit = self.entry_limit if entry_limit is None else entry_limit
        while stack and visited < limit:
            current = stack.pop()
            try:
                entries = os.scandir(current)
            except OSError as error:
                errors.append({"relative_path": str(current.relative_to(root)) or ".", "error": error.__class__.__name__})
                continue
            with entries:
                for entry in entries:
                    if visited >= limit:
                        break
                    visited += 1
                    relative = str(Path(entry.path).relative_to(root))
                    try:
                        if entry.is_symlink():
                            skipped_symlinks += 1
                            continue
                        stat = entry.stat(follow_symlinks=False)
                        key = (stat.st_dev, stat.st_ino)
                        if entry.is_dir(follow_symlinks=False):
                            directories += 1
                            if stat.st_dev != root_device:
                                skipped_mounts += 1
                            else:
                                stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            files += 1
                            if key not in seen:
                                seen.add(key)
                                total_bytes += stat.st_size
                    except OSError as error:
                        errors.append({"relative_path": relative, "error": error.__class__.__name__})
        reasons = []
        if stack or visited >= limit:
            reasons.append("entry_limit")
        if skipped_mounts:
            reasons.append("filesystem_boundary")
        if errors:
            reasons.append("inaccessible_entries")
        return {"bytes": total_bytes, "files": files, "directories": directories,
                "entries_scanned": visited, "skipped_symlinks": skipped_symlinks,
                "skipped_mounts": skipped_mounts, "complete": not reasons,
                "partial_reasons": reasons, "errors": errors[:20]}

    def scan(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"scope", "project_id", "max_entries"}:
            raise ValueError("Invalid storage diagnosis fields")
        scope = payload.get("scope")
        if scope not in {"runtime", "project"}:
            raise ValueError("Scope must be runtime or project")
        limit = payload.get("max_entries", self.entry_limit)
        if type(limit) is not int or limit < 1 or limit > self.entry_limit:
            raise ValueError(f"max_entries must be between 1 and {self.entry_limit}")
        if scope == "runtime":
            if "project_id" in payload:
                raise ValueError("Runtime diagnosis does not accept project_id")
            return {"scope": "runtime", "usage": self._scan(self.home, limit)}
        if "project_id" not in payload:
            raise ValueError("Project diagnosis requires project_id")
        project, workspace = self._project(payload["project_id"])
        metadata = self.home / "projects" / project.id
        return {"scope": "project", "project_id": project.id, "name": project.name,
                "workspace": str(workspace), "usage": self._scan(workspace, limit),
                "metadata_usage": self._scan(metadata, limit) if metadata.is_dir() and not metadata.is_symlink() else None}

    def report(self, project_id=None):
        projects = []
        candidates = [self._project(project_id)[0]] if project_id is not None else [row for row in self.hierarchy._all_projects_raw() if row.workspace_dir]
        for project in candidates:
            selected, workspace = self._project(project.id)
            metadata = self.home / "projects" / selected.id
            projects.append({"project_id": selected.id, "name": selected.name,
                             "workspace": str(workspace), "workspace_usage": self._scan(workspace),
                             "metadata_usage": self._scan(metadata) if metadata.is_dir() and not metadata.is_symlink() else None})
        return {"generated_at": datetime.now(timezone.utc).isoformat(),
                "entry_limit": self.entry_limit, "runtime_usage": self._scan(self.home),
                "projects": projects}
