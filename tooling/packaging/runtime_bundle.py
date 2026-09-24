from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

ENTRYPOINT_IMPORTS = (
    "gideon.integrations.mcp_core",
    "gideon.operations.desktop_smoke",
    "gideon.integrations.llm.acp_agent",
    "gideon.integrations.llm.anthropic",
    "gideon.integrations.llm.openai",
    "gideon.integrations.inbox_providers.slack_source",
)
MODULE_COLLECTIONS = (
    "gideon.sdk",
    "gideon.integrations.acp_bundles",
    "openai",
    "anthropic",
    "snowballstemmer",
    "slack_sdk",
    "openpyxl",
    "trafilatura",
)
RESOURCE_COLLECTIONS = ("trafilatura", "slack_sdk", "cron_descriptor")
OMITTED_MODULES = (
    "torch",
    "tensorflow",
    "faster_whisper",
    "faiss",
    "sentence_transformers",
    "transformers",
    "playwright",
    "pytest",
    "hypothesis",
    "black",
    "isort",
    "flake8",
    "mypy",
)


def require_native_interpreter() -> None:
    if os.environ.get("GIDEON_ALLOW_CROSS_ARCH") or platform.system() != "Darwin":
        return
    try:
        hardware = subprocess.run(
            ["sysctl", "-in", "hw.optional.arm64"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return
    interpreter = platform.machine()
    if hardware == "1" and interpreter != "arm64":
        raise SystemExit(
            f"Cannot package the {interpreter!r} interpreter on Apple Silicon. "
            "Use native arm64 Python to build the desktop runtime, or set "
            "GIDEON_ALLOW_CROSS_ARCH=1 for an intentional cross-architecture build."
        )


@dataclass(frozen=True)
class RuntimeBundlePlan:
    repository: Path

    @classmethod
    def for_recipe(cls, recipe: Path) -> RuntimeBundlePlan:
        return cls(recipe.resolve().parents[2])

    @property
    def import_root(self) -> Path:
        return self.repository / "runtime"

    @property
    def entrypoint(self) -> Path:
        return self.import_root / "gideon" / "__main__.py"

    def runtime_resources(self) -> list[tuple[str, str]]:
        from tooling.packaging.backend_bundle_manifest import data_files

        return data_files(self.repository)

    def provider_imports(self) -> list[str]:
        providers = self.import_root / "gideon" / "extensions" / "apps" / "native"
        modules: set[str] = set()
        for manifest in providers.glob("*/app.json"):
            try:
                descriptor = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            declaration = (descriptor.get("provider") or {}).get("implementation", "")
            module = declaration.partition(":")[0].strip()
            if module:
                modules.update((module, module.rpartition(".")[0] or module))
        return sorted(modules)

    def installer_inputs(self) -> dict[str, object]:
        from PyInstaller.utils.hooks import (
            collect_data_files,
            collect_submodules,
            copy_metadata,
        )

        imports = [*ENTRYPOINT_IMPORTS, *self.provider_imports()]
        from gideon.integrations.mcp_core import _AGGREGATED_CATEGORY_MODULES

        imports.extend(_AGGREGATED_CATEGORY_MODULES)
        for package in MODULE_COLLECTIONS:
            imports.extend(collect_submodules(package))

        from PyInstaller.config import CONF

        from tooling.packaging.backend_bundle_manifest import write_manifest

        resources = self.runtime_resources()
        inventory = write_manifest(
            self.repository, Path(CONF["workpath"]) / "runtime-bundle-manifest.json"
        )
        resources.append((str(inventory), "gideon"))
        resources.extend(copy_metadata("gideon-agent-harness"))
        for package in RESOURCE_COLLECTIONS:
            resources.extend(collect_data_files(package))
        resources.extend(collect_data_files("sqlite_vec", include_py_files=False))

        return {
            "pathex": [str(self.import_root)],
            "datas": resources,
            "hiddenimports": imports,
            "excludes": list(OMITTED_MODULES),
        }
