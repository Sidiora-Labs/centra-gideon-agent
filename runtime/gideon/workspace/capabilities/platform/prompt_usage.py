"""Prompt consumers from the binding truth and declared runtime catalogs."""

import json

from gideon.core.config.loader import config_dir
from gideon.extensions.apps import prompt_registry
from gideon.extensions.providers.prompt_use_cases import use_case_label
from gideon.integrations.prompt_providers.catalog import BUNDLED_PROMPTS


def prompt_usage(provider: str, name: str) -> dict:
    consumers = []
    complete = True
    declarations = {item.use_case: item for item in BUNDLED_PROMPTS}
    apps = {key: prompt_registry.get(key) for key in prompt_registry.use_cases()}
    path = config_dir() / "active_prompts.json"
    try:
        with path.open("rb") as source:
            raw = source.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("binding store exceeds inspection limit")
        active = json.loads(raw)
        if not isinstance(active, dict) or any(not isinstance(value, str) for value in active.values()):
            raise ValueError("invalid prompt bindings")
    except FileNotFoundError:
        active = {}
    except (OSError, ValueError):
        active = {}
        complete = False
    for use_case, reference in sorted(active.items()):
        if use_case in declarations or use_case in apps:
            if reference == f"{provider}:{name}":
                consumers.append({"kind": "binding", "id": use_case, "label": use_case_label(use_case)})
    if provider == "native":
        for entry in BUNDLED_PROMPTS:
            if entry.name == name:
                consumers.append({"kind": "native", "id": entry.use_case, "label": use_case_label(entry.use_case)})
    for use_case, entry in sorted(apps.items()):
        if entry and entry.provider == provider and entry.prompt_name == name:
            consumers.append({"kind": "app", "id": use_case, "label": f"{entry.app}: {use_case_label(use_case)}"})
    return {
        "version": 1,
        "provider": provider,
        "name": name,
        "consumers": consumers[:200],
        "total": len(consumers),
        "complete": complete,
        "deletable": complete and not consumers,
    }
