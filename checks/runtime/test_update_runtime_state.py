import subprocess

import pytest
from aiohttp import web

from gideon.operations import self_update as update


@pytest.mark.asyncio
async def test_real_release_http_cache_conditionals_and_offline_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    requests = []
    releases = [
        {
            "tag_name": "v2.0.0-rc.1",
            "name": "Preview",
            "body": "notes",
            "prerelease": True,
        },
        {"tag_name": "v1.9.0", "name": "Stable", "body": "notes"},
    ]

    async def catalog(request):
        requests.append((request.path, request.headers.get("If-None-Match")))
        if request.headers.get("If-None-Match") == '"edition-1"':
            return web.Response(status=304)
        payload = releases[1] if request.path.endswith("/latest") else releases
        return web.json_response(payload, headers={"ETag": '"edition-1"'})

    app = web.Application()
    app.router.add_get("/releases", catalog)
    app.router.add_get("/releases/latest", catalog)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/releases"
    monkeypatch.setattr(update, "_RELEASES_LIST_URL", address)
    monkeypatch.setattr(update, "_RELEASES_LATEST_URL", address + "/latest")
    try:
        latest = await update.fetch_latest_release()
        assert latest["tag"] == "v1.9.0"
        assert await update.fetch_latest_release() == latest
        assert await update.resolve_target("stable") == "v1.9.0"
        assert await update.resolve_target("beta") == "v2.0.0-rc.1"
        assert await update.resolve_target("nightly", "1.9.0") == "v1.9.0"
        monkeypatch.setenv("GIDEON_INSTALL_KIND", "container")
        status = await update.build_update_status("v1.8.0")
        assert status["update_available"] and status["apply_method"] == "instructions"
        assert status["instructions"] == update.container_instructions()
        assert requests[0] == ("/releases/latest", None)
        assert requests[1] == ("/releases/latest", '"edition-1"')
        assert requests[2] == ("/releases", None)
        assert requests[3] == ("/releases", '"edition-1"')
    finally:
        await runner.cleanup()
    assert await update.fetch_latest_release() == latest
    assert await update.resolve_target("beta") == "v2.0.0-rc.1"
    assert update._cache_path().is_file() and update._list_cache_path().is_file()


def git(path, *args):
    return subprocess.run(
        ["git", *args], cwd=path, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.mark.asyncio
async def test_actual_checkout_fetch_branch_selection_changes_and_update(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Gideon checks")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "checks@gideon.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Gideon checks")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "checks@gideon.invalid")
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    (origin / "tracked").write_text("first")
    git(origin, "add", "tracked")
    git(origin, "commit", "-m", "Initial fixture")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(origin), str(clone))
    assert update.current_branch(str(clone)) == "main"
    assert update.resolve_default_branch(str(clone)) == "main"
    assert update.git_is_up_to_date(str(clone), "main")
    (origin / "tracked").write_text("second")
    git(origin, "commit", "-am", "Advance fixture")
    assert await update.commits_behind_upstream(str(clone)) == 1
    assert update.git_fetch(str(clone), "main").returncode == 0
    assert not update.git_is_up_to_date(str(clone), "main")
    (clone / "tracked").write_text("local")
    (clone / "untracked").write_text("keep")
    assert update.git_tracked_changes(str(clone)) == [" M tracked"]
    plan = update.SourcePlan("branch", "origin/main")
    assert (await update.plan_source_update(str(clone), "nightly")).paused
    assert update.git_merge_ff_only(str(clone), plan.ref).returncode != 0
    assert (clone / "tracked").read_text() == "local"
    git(clone, "checkout", "--", "tracked")
    assert update.git_is_fast_forward(str(clone), plan.ref)
    assert update.git_merge_ff_only(str(clone), plan.ref).returncode == 0
    assert (clone / "tracked").read_text() == "second"
    assert (clone / "untracked").read_text() == "keep"
    assert await update.commits_behind_upstream(str(clone)) == 0
    git(clone, "checkout", "--detach")
    assert update.current_branch(str(clone)) == ""
    assert update.resolve_default_branch(str(clone)) == "main"
    assert await update.commits_behind_upstream(str(clone)) is None


def test_actual_cache_errors_and_install_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    update.write_release_cache({"tag": "v1"})
    update._cache_path().write_text("{broken")
    assert update.read_release_cache() == {}
    update._cache_path().unlink()
    update._cache_path().mkdir()
    update.write_release_cache({"tag": "cannot-write"})
    assert update.read_release_cache() == {}
    source = tmp_path / "source"
    source.mkdir()
    nested = source / "Gideon"
    nested.mkdir()
    (source / ".git").write_text("gitdir: ../actual")
    (nested / "pyproject.toml").write_text('[project]\nname="gideon"')
    assert update.git_root(str(nested)) == str(source)
    assert update.package_root(str(source)) == str(nested)
    assert (
        update._run_git(["status"], cwd=str(tmp_path / "missing"), timeout=1).returncode
        == 127
    )
