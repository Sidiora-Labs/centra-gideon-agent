"""Shared connection references in Gideon's existing configuration document."""

import dataclasses
import fnmatch
import json
import re
from urllib.parse import urlsplit

from gideon.core.atomic_write import atomic_write
from gideon.core.config.loader import config_dir, config_path
from gideon.integrations.llm.catalog import ModelManager
from gideon.integrations.llm.credentials import CredentialStore

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z")


class ConnectionError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def document():
    try:
        data = json.loads(config_path().read_text())
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as error:
        raise ConnectionError("Provider configuration is unreadable", 503) from error
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("provider_connections", {}), dict)
        or not isinstance(data.get("providers", []), list)
    ):
        raise ConnectionError("Provider configuration is invalid", 503)
    return data


def allowed(model, policy):
    patterns = policy.get("patterns", [])
    mode = policy.get("mode", "all")
    if mode == "all" or not patterns:
        return True
    matches = any(fnmatch.fnmatchcase(model, pattern) for pattern in patterns)
    return matches if mode == "allow" else not matches


def projection(data=None):
    data = document() if data is None else data
    providers = data.get("providers", [])
    rows = []
    for key, row in sorted(data.get("provider_connections", {}).items()):
        rows.append(
            {
                **{
                    field: row[field]
                    for field in (
                        "label",
                        "base_url",
                        "credential_ref",
                        "model_access",
                        "revision",
                    )
                },
                "id": key,
                "bindings": [
                    p["name"]
                    for p in providers
                    if p.get("options", {}).get("connection_id") == key
                ],
            }
        )
    return {
        "version": 1,
        "connections": rows,
        "providers": [
            {
                "name": p["name"],
                "connection_id": p.get("options", {}).get("connection_id"),
            }
            for p in providers
        ],
        "credentials": [
            credential.name for credential in CredentialStore(config_dir()).list()
        ],
    }


def mutate(identifier, body, *, operation="save", provider=None):
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ConnectionError("Invalid connection identifier")
    data = document()
    connections = data.setdefault("provider_connections", {})
    previous = connections.get(identifier)
    revision = previous.get("revision", 0) if previous else 0
    if type(body.get("revision")) is not int or body["revision"] != revision:
        raise ConnectionError("Connection changed; reload before saving", 409)
    bindings = [
        p
        for p in data.get("providers", [])
        if p.get("options", {}).get("connection_id") == identifier
    ]
    if operation == "save":
        endpoint = body.get("base_url", "")
        label = body.get("label", "")
        reference = body.get("credential_ref") or None
        policy = body.get("model_access", {"mode": "all", "patterns": []})
        if not isinstance(endpoint, str) or len(endpoint) > 500:
            raise ConnectionError("Invalid endpoint")
        url = urlsplit(endpoint)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ConnectionError(
                "Endpoint must be HTTP(S) without credentials or query parameters"
            )
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 100:
            raise ConnectionError("Label must contain 1..100 characters")
        if reference is not None and (
            not isinstance(reference, str)
            or not CredentialStore(config_dir()).has(reference)
        ):
            raise ConnectionError("Credential reference does not exist")
        if not isinstance(policy, dict) or policy.get("mode") not in {
            "all",
            "allow",
            "deny",
        }:
            raise ConnectionError("Invalid model access mode")
        patterns = policy.get("patterns", [])
        if (
            not isinstance(patterns, list)
            or len(patterns) > 32
            or any(not isinstance(p, str) or not 1 <= len(p) <= 128 for p in patterns)
        ):
            raise ConnectionError(
                "Model patterns must be at most 32 strings of 1..128 characters"
            )
        connections[identifier] = {
            "label": label.strip(),
            "base_url": endpoint,
            "credential_ref": reference,
            "model_access": {
                "mode": policy["mode"],
                "patterns": list(dict.fromkeys(patterns)),
            },
            "revision": revision + 1,
        }
    elif operation == "delete":
        if not previous:
            raise ConnectionError("Connection not found", 404)
        if bindings:
            raise ConnectionError(
                "Unbind providers before deleting this connection", 409
            )
        del connections[identifier]
    elif operation == "bind":
        if not previous:
            raise ConnectionError("Connection not found", 404)
        row = next(
            (p for p in data.get("providers", []) if p.get("name") == provider), None
        )
        if row is None:
            raise ConnectionError("Provider not found", 404)
        if type(body.get("bound")) is not bool:
            raise ConnectionError("bound must be a boolean")
        options = row.setdefault("options", {})
        if options.get("connection_id") not in {None, identifier}:
            raise ConnectionError("Provider is bound to another connection", 409)
        if body["bound"]:
            options["connection_id"] = identifier
        else:
            options.pop("connection_id", None)
        previous["revision"] += 1
    else:
        raise ConnectionError("Unknown connection operation")
    atomic_write(config_path(), json.dumps(data, indent=2) + "\n", fsync=True)
    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    for row in data.get("providers", []):
        entry = registry._entries.get(row.get("name"))
        if entry is not None:
            registry._entries[entry.name] = dataclasses.replace(
                entry, options=dict(row.get("options", {}))
            )
    return projection(data)


def effective_entry(entry):
    identifier = entry.options.get("connection_id")
    if not identifier:
        return entry
    connection = document().get("provider_connections", {}).get(identifier)
    if connection is None:
        raise ConnectionError("Provider connection is missing", 503)
    options = {
        key: value
        for key, value in entry.options.items()
        if key not in {"api_key", "apiKey", "endpoint", "base_url"}
    }
    options.update(
        base_url=connection["base_url"],
        endpoint=connection["base_url"],
        model_access=connection["model_access"],
    )
    return dataclasses.replace(
        entry, options=options, credential=connection["credential_ref"]
    )


class ScopedCatalog(ModelManager):
    def __init__(self, catalog, policy):
        self.catalog, self.policy = catalog, policy
        self._models = None

    async def full_catalog(self):
        if self._models is None:
            self._models = await self.catalog.list_models()
        return list(self._models)

    async def list_models(self):
        return [
            model
            for model in await self.full_catalog()
            if allowed(model.id, self.policy)
        ]

    async def search_catalog(self, query):
        return [
            model
            for model in await self.catalog.search_catalog(query)
            if allowed(model.id, self.policy)
        ]

    def pull_model(self, model_id):
        return self.catalog.pull_model(model_id)

    async def delete_model(self, model_id):
        return await self.catalog.delete_model(model_id)

    async def show_model(self, model_id):
        return await self.catalog.show_model(model_id)

    async def test_connection(self):
        return await self.catalog.test_connection()
