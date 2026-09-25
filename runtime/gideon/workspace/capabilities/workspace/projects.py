"""Project discovery and safe local templates over the canonical project store."""

import fcntl
import itertools
import json
import os
import re
import shutil
import sqlite3
import tomllib
from pathlib import Path

from gideon.engine.tasks.hierarchy import HierarchyStore

from .store import ConflictError

PYTHON_APP = """import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/health':
            self.send_error(404)
            return
        data = json.dumps({'status': 'ok'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

HTTPServer(('127.0.0.1', int(os.environ.get('PORT', '8000'))), Handler).serve_forever()
"""
NODE_APP = """import http from 'node:http';
const server = http.createServer((request, response) => {
  if (request.url !== '/health') {
    response.writeHead(404); response.end('Not found'); return;
  }
  response.writeHead(200, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify({ status: 'ok' }));
});
server.listen(Number(process.env.PORT || 8000), '127.0.0.1');
"""


class ProjectService:
    def __init__(self, root, *, allowed_roots, hierarchy=None):
        self.root = Path(root)
        self.allowed_roots = [Path(p).resolve() for p in allowed_roots]
        self.hierarchy = hierarchy if hierarchy is not None else HierarchyStore()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "project-operations.sqlite3"
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS operations(request_id TEXT PRIMARY KEY, input TEXT NOT NULL, project_id TEXT)"
            )
        os.chmod(self.db_path, 0o600)

    def _path(self, raw):
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            raise ValueError("Workspace must be an absolute path")
        path = Path(raw).resolve(strict=True)
        if not path.is_dir() or not any(
            path.is_relative_to(root) for root in self.allowed_roots
        ):
            raise ValueError("Workspace outside allowed roots")
        return path

    def _read(self, path):
        if path.is_symlink():
            raise ValueError("Project metadata symlinks are not accepted")
        if path.stat().st_size > 262144:
            raise ValueError("Project metadata exceeds 256 KiB")
        return path.read_text(encoding="utf-8")

    def detect(self, workspace):
        path = self._path(workspace)
        result = {
            "workspace": str(path),
            "name": path.name,
            "types": [],
            "commands": {},
            "ports": [],
            "has_git": (path / ".git").exists(),
            "source_files": [],
        }
        package = path / "package.json"
        if package.exists():
            data = json.loads(self._read(package))
            if not isinstance(data, dict):
                raise ValueError("package.json must contain an object")
            result["types"].append("node")
            result["source_files"].append("package.json")
            scripts = data.get("scripts", {})
            if not isinstance(scripts, dict) or len(scripts) > 100:
                raise ValueError("Invalid or excessive package scripts")
            for name, command in scripts.items():
                if not isinstance(command, str) or len(command) > 8192:
                    raise ValueError("Invalid package command")
                result["commands"][name] = command
                result["ports"].extend(
                    int(port)
                    for port in re.findall(r"--port[ =](\d{2,5})\b", command)
                    if 0 < int(port) <= 65535
                )
        for name, kind in [
            ("pyproject.toml", "python"),
            ("Cargo.toml", "rust"),
            ("Package.swift", "swift"),
        ]:
            file = path / name
            if file.exists():
                text = self._read(file)
                if name.endswith(".toml"):
                    tomllib.loads(text)
                result["types"].append(kind)
                result["source_files"].append(name)
        entries = list(itertools.islice(path.iterdir(), 257))
        result["directory_entries_truncated"] = len(entries) > 256
        for entry in sorted(entries[:256], key=lambda p: p.name):
            if not entry.is_symlink() and entry.name.endswith(
                (".xcodeproj", ".xcworkspace")
            ):
                result["types"].append("xcode")
                result["source_files"].append(entry.name)
        if (path / "app.py").is_file():
            self._read(path / "app.py")
            result["types"].append("python")
            result["commands"].setdefault("start", "python3 app.py")
            result["source_files"].append("app.py")
        result["types"] = sorted(set(result["types"]))
        result["ports"] = sorted(set(result["ports"]))
        return result

    def templates(self):
        return [
            {
                "id": "python-http",
                "name": "Python HTTP service",
                "entrypoint": "app.py",
                "command": "python3 app.py",
                "requires": "python3",
                "available": shutil.which("python3") is not None,
            },
            {
                "id": "node-http",
                "name": "Node HTTP service",
                "entrypoint": "server.mjs",
                "command": "node server.mjs",
                "requires": "node",
                "available": shutil.which("node") is not None,
            },
        ]

    def get(self, project_id):
        project = self.hierarchy.get_project(project_id)
        if project is None:
            raise FileNotFoundError("Project not found")
        return {
            "project": project.to_dict(),
            "detection": self.detect(project.workspace_dir),
        }

    def list(self):
        result = []
        for project in self.hierarchy.list_projects():
            if not project.workspace_dir:
                continue
            try:
                result.append(self.get(project.id))
            except (ValueError, OSError):
                continue
        return result

    def _validate(self, payload, fields):
        if not isinstance(payload, dict) or set(payload) != set(fields):
            raise ValueError("Invalid project operation fields")
        for key, value in payload.items():
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > (4096 if key in ("workspace", "parent") else 256)
            ):
                raise ValueError(f"Invalid {key}")

    def register(self, payload):
        self._validate(payload, ["name", "workspace", "request_id"])
        workspace = self._path(payload["workspace"])
        self.detect(str(workspace))
        return self._operation(payload, workspace)

    def scaffold(self, payload):
        self._validate(
            payload, ["name", "parent", "directory", "template", "request_id"]
        )
        parent = self._path(payload["parent"])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", payload["directory"]):
            raise ValueError("Directory must be a single safe segment")
        if payload["template"] not in {entry["id"] for entry in self.templates()}:
            raise ValueError("Unknown project template")
        return self._operation(payload, parent / payload["directory"])

    def _operation(self, payload, workspace):
        encoded = json.dumps(payload, sort_keys=True)
        with (
            (self.root / "project-operations.lock").open("a") as lock,
            sqlite3.connect(self.db_path) as db,
        ):
            fcntl.flock(lock, fcntl.LOCK_EX)
            row = db.execute(
                "SELECT input,project_id FROM operations WHERE request_id=?",
                (payload["request_id"],),
            ).fetchone()
            if row:
                if row[0] != encoded:
                    raise ConflictError(
                        "Request ID already used for another project operation"
                    )
                if row[1]:
                    return self.get(row[1])
            else:
                if "template" in payload and workspace.exists():
                    raise ConflictError("Scaffold target already exists")
                db.execute(
                    "INSERT INTO operations VALUES(?,?,NULL)",
                    (payload["request_id"], encoded),
                )
                db.commit()
            existing = self.hierarchy.get_project_by_name(payload["name"])
            if existing and existing.workspace_dir != str(workspace):
                raise ConflictError("Project name already belongs to another workspace")
            if "template" in payload:
                marker = workspace / ".gideon-scaffold.json"
                if not workspace.exists():
                    workspace.mkdir()
                    with marker.open("x") as out:
                        json.dump(
                            {
                                "request_id": payload["request_id"],
                                "template": payload["template"],
                            },
                            out,
                        )
                elif (
                    workspace.is_symlink()
                    or not marker.is_file()
                    or marker.is_symlink()
                    or json.loads(self._read(marker)).get("request_id")
                    != payload["request_id"]
                ):
                    raise ConflictError(
                        "Scaffold target is not owned by this operation"
                    )
                if payload["template"] == "python-http":
                    files = {
                        "app.py": PYTHON_APP,
                        "pyproject.toml": '[project]\nname = "local-http-service"\nversion = "0.1.0"\n',
                    }
                else:
                    files = {
                        "server.mjs": NODE_APP,
                        "package.json": json.dumps(
                            {
                                "name": payload["directory"].lower(),
                                "version": "0.1.0",
                                "private": True,
                                "type": "module",
                                "scripts": {"start": "node server.mjs"},
                            }
                        )
                        + "\n",
                    }
                for name, text in files.items():
                    file = workspace / name
                    if file.exists() or file.is_symlink():
                        if file.is_symlink() or self._read(file) != text:
                            raise ConflictError(
                                "Scaffold file was changed; refusing overwrite"
                            )
                    else:
                        with file.open("x") as out:
                            out.write(text)
            project = existing or self.hierarchy.create_project(
                name=payload["name"], workspace_dir=str(workspace)
            )
            db.execute(
                "UPDATE operations SET project_id=? WHERE request_id=?",
                (project.id, payload["request_id"]),
            )
            return self.get(project.id)
