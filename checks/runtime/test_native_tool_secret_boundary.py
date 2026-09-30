from __future__ import annotations

import pytest

from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider


@pytest.mark.asyncio
async def test_protected_roots_and_owner_secret_boundary(tmp_path, monkeypatch):
    home = tmp_path / "user-home"
    gideon_home = tmp_path / "gideon-home"
    workspace = tmp_path / "workspace"
    outside = tmp_path / "alternate-root"
    for directory in (home / ".ssh", gideon_home / "credentials", workspace, outside):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(gideon_home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")

    marker = "protected-material-7c9c6e"
    gateway_value = "gateway-password-2a44c1"
    owner_value = "owner-build-token-8d931f"
    reserved_value = "browser-profile-token-5c37e0"
    (home / ".ssh" / "id_ed25519").write_text(marker, encoding="utf-8")
    (workspace / ".env").write_text(f"TOKEN={marker}\n", encoding="utf-8")
    (workspace / "tls.pem").write_text(marker, encoding="utf-8")
    (outside / "ordinary.txt").write_text(marker, encoding="utf-8")
    (workspace / "key-link").symlink_to(home / ".ssh" / "id_ed25519")
    (workspace / "outside-link").symlink_to(outside / "ordinary.txt")
    monkeypatch.setenv("CENTRA_GATEWAY_PASSWORD", gateway_value)

    from gideon.core.config import credentials

    credentials.put_secret_value("OWNER_BUILD_TOKEN", owner_value)
    credentials.put_secret_value("BROWSE_PROFILE_KEY_TEST", reserved_value)
    tools = NativeBuiltinToolProvider(workspace, sandbox_mode="off")
    try:
        for path in (
            ".env",
            "tls.pem",
            "key-link",
            "outside-link",
            str(outside / "ordinary.txt"),
            "~/.ssh/id_ed25519",
            "bad\nname.txt",
        ):
            result = await tools.invoke("read_file", {"path": path})
            assert not result.success, path
            assert marker not in (result.output or ""), path

        listing = await tools.invoke("list_dir", {"path": "."})
        assert listing.success
        assert not {".env", "tls.pem", "key-link", "outside-link"} & set(
            (listing.output or "").splitlines()
        )

        globbed = await tools.invoke("glob", {"pattern": "**/*"})
        assert globbed.success
        assert not {".env", "tls.pem", "key-link", "outside-link"} & set(
            (globbed.output or "").splitlines()
        )
        searched = await tools.invoke("grep", {"query": marker})
        assert searched.success and marker not in (searched.output or "")

        before = (workspace / ".env").read_text(encoding="utf-8")
        denied_write = await tools.invoke(
            "write_file", {"path": ".env", "content": "replaced"}
        )
        assert not denied_write.success
        assert (workspace / ".env").read_text(encoding="utf-8") == before

        command = await tools.invoke(
            "bash",
            {"command": "printf '%s' '{{secret:OWNER_BUILD_TOKEN}}'"},
        )
        assert command.success
        assert owner_value not in (command.output or "")
        assert "REDACTED" in (command.output or "")

        missing = await tools.invoke(
            "bash",
            {
                "command": "printf x > must-not-run.txt; printf '%s' '{{secret:MISSING_TOKEN}}'"
            },
        )
        assert not missing.success
        assert not (workspace / "must-not-run.txt").exists()

        reserved = await tools.invoke(
            "bash", {"command": "printf '%s' '{{secret:BROWSE_PROFILE_KEY_TEST}}'"}
        )
        assert not reserved.success
        assert reserved_value not in (reserved.output or "")

        inherited = await tools.invoke("bash", {"command": "env"})
        assert inherited.success
        assert gateway_value not in (inherited.output or "")

        gideon_home_path = await tools.invoke(
            "bash",
            {
                "command": "node -e \"console.log(process.env.GIDEON_HOME+'/credentials/token')\""
            },
        )
        assert not gideon_home_path.success
        assert "credential" in (gideon_home_path.error or "").lower()
    finally:
        credentials.delete_secret_value("OWNER_BUILD_TOKEN")
        credentials.delete_secret_value("BROWSE_PROFILE_KEY_TEST")
