import json

from gideon.engine.hooks import ScriptHookStore
from gideon.extensions.apps.app_hooks import reconcile_app_hooks
from gideon.extensions.apps.manager import InstalledApp, _write_installed, app_dir
from gideon.extensions.apps.manifest import AppManifest


def test_app_hooks_follow_installed_lifecycle_without_touching_user_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    manifest = AppManifest.from_dict(
        {
            "name": "test-app",
            "version": "1.0.0",
            "displayName": "Test App",
            "description": "Receives tool completion events",
            "hooks": [
                {
                    "name": "tool-finished",
                    "event": "PostToolUse",
                    "provider": "bash",
                    "providerConfig": {"command": "true"},
                }
            ],
        }
    )
    assert manifest.validate() == []
    app = app_dir("test-app")
    app.mkdir(parents=True)
    (app / "app.json").write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    meta = InstalledApp(name="test-app", version="1.0.0", displayName="Test App", enabled=True)
    _write_installed("test-app", meta)
    store = ScriptHookStore(tmp_path)
    user = store.create({"id": "my-hook", "event": "PostToolUse", "provider": "bash", "provider_config": {"command": "true"}})

    reconcile_app_hooks(store)
    contributed = store.get("app:test-app:tool-finished")
    assert contributed is not None
    assert contributed.provider_config == {"command": "true"}
    assert store.get(user.id) is not None

    meta.enabled = False
    _write_installed("test-app", meta)
    reconcile_app_hooks(store)
    assert store.get("app:test-app:tool-finished") is None
    assert store.get(user.id) is not None
