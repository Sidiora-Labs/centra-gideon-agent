from __future__ import annotations

import json
from pathlib import Path

from gideon.extensions.apps import manager
from gideon.integrations.local_models.sidecar import SidecarInstall


def test_installer_reads_only_the_declared_sidecar_package_list(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    app = manager.app_dir("service-client")
    app.mkdir(parents=True)
    (app / "app.json").write_text(
        json.dumps(
            {
                "name": "service-client",
                "version": "1.0.0",
                "displayName": "Service Client",
                "description": "Uses an isolated provider engine.",
                "provider": {
                    "type": "model",
                    "implementation": "provider:create_provider",
                    "execution": "sidecar",
                },
                "dependencies": {
                    "pythonDependencies": ["gateway-addon>=1"],
                    "sidecarDependencies": ["engine-addon>=2"],
                },
            }
        ),
        encoding="utf-8",
    )

    install = SidecarInstall.for_app("service-client")

    assert install is not None
    assert install.requirements == ["engine-addon>=2"]
    assert install.venv == app / "venv"
