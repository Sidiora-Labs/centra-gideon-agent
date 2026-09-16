"""Connector catalog + requirements resolution + setup-skill + ledger (AP-3).

Builds a real ``.gideon`` from a seeded AUTHOR home (AP-1's ``build_pack``), grafts the
kinds AP-1 doesn't export (a ``connectors.json`` declaration, a ``setup/SKILL.md``), and
imports into a SEPARATE, isolated IMPORTER home — asserting the AP-3 contract:

* the connector catalog seeds + loads;
* a connector resolves via configure (credential in the store + a server in mcp.json),
  substitute (same-category rewrite), and skip (``connector_missing:<name>`` marker), with
  the install still succeeding on skip;
* a collected credential NEVER lands in a plaintext config field or the pack;
* ``setup/SKILL.md`` installs through the guarded path (``.gideon-lock.json``) and is
  re-runnable (the ledger keeps ``setup_pending`` true).

Both homes bind ``GIDEON_HOME`` (the robust lever — every store + the credential
store read it live) so no test touches the real ``~/.gideon``.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from gideon.extensions.packs.build import build_pack
from gideon.extensions.packs.connectors import (
    ConnectorResolutionError,
    catalog_by_category,
    load_catalog,
    missing_marker,
    resolve_connector,
    seed_catalog,
)
from gideon.extensions.packs.import_ import import_pack
from gideon.extensions.packs.installed import load_installed


def _seed_author_home(home: Path) -> None:
    sk = home / "skills" / "cfo-report"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        "---\nname: cfo-report\ndescription: Build a monthly CFO report\n---\n# Report\nSteps.\n"
    )


@pytest.fixture
def built_pack(tmp_path, monkeypatch):
    author = tmp_path / "author"
    author.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(author))
    _seed_author_home(author)
    out = tmp_path / "cfo.gideon"
    build_pack(["skill:cfo-report"], name="cfo", version="1.0.0", out_path=out)
    monkeypatch.delenv("GIDEON_HOME", raising=False)
    return out


@pytest.fixture
def importer_home(tmp_path, monkeypatch):
    home = tmp_path / "importer"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    return home


def _read_pack(path: Path) -> tuple[dict, dict[str, bytes]]:
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            members[name] = zf.read(name)
    return json.loads(members["pack.json"]), members


def _write_pack(path: Path, manifest: dict, members: dict[str, bytes]) -> None:
    members = dict(members)
    members["pack.json"] = json.dumps(manifest, indent=2).encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, raw in members.items():
            zf.writestr(name, raw)


def _add_connectors(path: Path, decls: list[dict]) -> None:
    manifest, members = _read_pack(path)
    members["connectors.json"] = json.dumps(decls).encode("utf-8")
    _write_pack(path, manifest, members)


def _add_setup_skill(path: Path, body: str) -> None:
    manifest, members = _read_pack(path)
    members["setup/SKILL.md"] = body.encode("utf-8")
    _write_pack(path, manifest, members)


def test_catalog_seeds_and_loads(importer_home):
    assert not (importer_home / "connector_catalog.json").exists()
    entries = seed_catalog(importer_home)
    assert (importer_home / "connector_catalog.json").is_file()
    assert entries and all(e.name and e.category for e in entries)
    names = {e.name for e in load_catalog(importer_home)}
    assert "filesystem" in names and "web-search" in names
    seed_catalog(importer_home)
    assert {e.name for e in load_catalog(importer_home)} == names


def test_catalog_by_category(importer_home):
    seed_catalog(importer_home)
    fs = catalog_by_category("filesystem", importer_home)
    assert fs and all(e.category == "filesystem" for e in fs)


def test_configure_saves_credential_and_writes_server(importer_home):
    seed_catalog(importer_home)
    decl = {"name": "web-search", "category": "search"}
    res = resolve_connector(
        decl,
        mode="configure",
        credentials={"SEARCH_API_KEY": "sk-secret-123"},
        home=importer_home,
    )
    assert res.mode == "configure"
    assert res.server_name == "web-search"
    assert res.credentials_saved == ["SEARCH_API_KEY"]

    mcp = json.loads((importer_home / "mcp.json").read_text())
    assert "web-search" in mcp["mcpServers"]

    from gideon.integrations.llm.credentials import CredentialStore

    store = CredentialStore(importer_home)
    assert store.has("SEARCH_API_KEY")
    assert store.resolve("SEARCH_API_KEY").secret == "sk-secret-123"


def test_configure_refuses_without_credential_value(importer_home):
    seed_catalog(importer_home)
    decl = {"name": "web-search", "category": "search"}
    with pytest.raises(ConnectorResolutionError):
        resolve_connector(decl, mode="configure", credentials={}, home=importer_home)
    assert not (importer_home / "mcp.json").exists()


def test_credential_never_lands_in_config_or_pack(importer_home):
    seed_catalog(importer_home)
    resolve_connector(
        {"name": "web-search", "category": "search"},
        mode="configure",
        credentials={"SEARCH_API_KEY": "sk-canary-XYZ"},
        home=importer_home,
    )
    cfg = importer_home / "config.json"
    if cfg.exists():
        assert "sk-canary-XYZ" not in cfg.read_text()
    assert "sk-canary-XYZ" not in (importer_home / "mcp.json").read_text()
    creds_json = importer_home / "credentials.json"
    assert creds_json.is_file()
    assert "sk-canary-XYZ" not in creds_json.read_text()
    env = importer_home / ".env"
    assert "sk-canary-XYZ" in env.read_text()
    import stat

    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_substitute_same_category_rewrites(importer_home):
    seed_catalog(importer_home)
    res = resolve_connector(
        {"name": "authors-search", "category": "search"},
        mode="substitute",
        substitute="web-search",
        home=importer_home,
    )
    assert res.mode == "substitute"
    assert res.server_name == "web-search"
    assert not (importer_home / "mcp.json").exists()


def test_substitute_different_category_refused(importer_home):
    seed_catalog(importer_home)
    with pytest.raises(ConnectorResolutionError):
        resolve_connector(
            {"name": "authors-search", "category": "search"},
            mode="substitute",
            substitute="filesystem",
            home=importer_home,
        )


def test_skip_records_missing_marker(importer_home):
    res = resolve_connector(
        {"name": "gmail", "category": "email"}, mode="skip", home=importer_home
    )
    assert res.mode == "skip"
    assert res.marker == missing_marker("gmail") == "connector_missing:gmail"


def test_import_with_skipped_connector_still_succeeds(built_pack, importer_home):
    _add_connectors(built_pack, [{"name": "gmail", "category": "email"}])
    plan = import_pack(built_pack)
    assert (importer_home / "skills" / "cfo-report" / "SKILL.md").is_file()
    markers = [r["marker"] for r in plan.connector_resolutions if r["marker"]]
    assert "connector_missing:gmail" in markers

    installed = {p.name: p for p in load_installed(importer_home)}
    assert "cfo" in installed
    assert "connector_missing:gmail" in installed["cfo"].connector_markers


def test_import_configures_connector_from_choice(built_pack, importer_home):
    _add_connectors(built_pack, [{"name": "web-search", "category": "search"}])
    plan = import_pack(
        built_pack,
        connector_choices={
            "web-search": {
                "mode": "configure",
                "credentials": {"SEARCH_API_KEY": "sk-live-1"},
            }
        },
    )
    modes = {r["name"]: r["mode"] for r in plan.connector_resolutions}
    assert modes["web-search"] == "configure"
    assert (
        "web-search"
        in json.loads((importer_home / "mcp.json").read_text())["mcpServers"]
    )
    from gideon.integrations.llm.credentials import CredentialStore

    assert (
        CredentialStore(importer_home).resolve("SEARCH_API_KEY").secret == "sk-live-1"
    )


def test_import_bad_configure_degrades_to_skip_marker(built_pack, importer_home):
    _add_connectors(built_pack, [{"name": "web-search", "category": "search"}])
    plan = import_pack(
        built_pack,
        connector_choices={"web-search": {"mode": "configure", "credentials": {}}},
    )
    assert (importer_home / "skills" / "cfo-report" / "SKILL.md").is_file()
    markers = [r["marker"] for r in plan.connector_resolutions if r["marker"]]
    assert "connector_missing:web-search" in markers


def test_setup_skill_installs_guarded_and_is_rerunnable(built_pack, importer_home):
    _add_setup_skill(
        built_pack,
        "---\nname: cfo-setup\ndescription: Bind your finance folder\n---\n"
        "# Setup\nAsk which folder holds finance CSVs.\n",
    )
    plan = import_pack(built_pack)
    assert plan.setup_skill
    setup_dir = importer_home / "skills" / plan.setup_skill
    assert (setup_dir / "SKILL.md").is_file()
    assert (setup_dir / ".gideon-lock.json").is_file()

    installed = {p.name: p for p in load_installed(importer_home)}
    assert installed["cfo"].setup_skill == plan.setup_skill
    assert installed["cfo"].setup_pending is True


def test_dangerous_setup_skill_blocks_import(built_pack, importer_home):
    _add_setup_skill(
        built_pack,
        "---\nname: cfo-setup\ndescription: hides steering ‮text\n---\n# Setup\n",
    )
    from gideon.extensions.packs.import_ import PackImportRefused

    with pytest.raises(PackImportRefused) as exc:
        import_pack(built_pack, consent=True)
    assert exc.value.reason == "dangerous"
    assert not (importer_home / "skills" / "cfo-report").exists()
