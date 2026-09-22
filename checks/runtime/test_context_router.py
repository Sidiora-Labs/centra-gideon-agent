"""§7 routed-context provider — the pure assembler + the marker-fenced adapters,
plus the two live endpoints (``GET /api/context`` and the regenerate POST).

The assembler tests pin the three invariants the soul guardrail cares about:
tier ordering (rules top / L0 catalog bottom), the structural MEMORY-vs-KNOWLEDGE
boundary (distinct headings; knowledge never inlines a body), and honest,
no-silent-cap unloaded notes. The ``apply_block`` tests pin the replace-in-place
contract (idempotent, first-write appends, malformed fence raises). The endpoint
tests pin the consent gate (adapters off → 403; no workspace → 400) and that a
write only touches bytes inside the fence.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.assurance.legibility import context_router as cr


def _routed(**kw) -> cr.RoutedContext:
    base = dict(
        project_id="p1",
        project_name="Website",
        brief="Ship the marketing site.",
        instructions="Validate as a user before calling anything done.",
        memories=[
            {
                "text": "User prefers terse commits.",
                "source": "chat",
                "created_at": "2026-07-01T00:00:00",
            }
        ],
        skills=[{"key": "gideon-api", "description": "Offline API reference."}],
        knowledge=[
            {"id": "k1", "title": "Brand guide", "summary": "Colors, type, voice."}
        ],
    )
    base.update(kw)
    return cr.assemble(**base)


def test_assemble_rules_carry_brief_then_instructions():
    routed = _routed()
    assert routed.rules.index("Ship the marketing site.") < routed.rules.index(
        "Operating procedure"
    )
    assert "Validate as a user" in routed.rules


def test_render_orders_rules_top_and_catalog_bottom():
    body = _routed().render()
    i_rules = body.index(cr.RULES_HEADING)
    i_mem = body.index(cr.MEMORY_HEADING)
    i_know = body.index(cr.KNOWLEDGE_HEADING)
    i_unloaded = body.index(cr.UNLOADED_HEADING)
    assert i_rules < i_mem < i_know < i_unloaded
    assert i_unloaded == max(i_rules, i_mem, i_know, i_unloaded)


def test_memory_and_knowledge_headings_are_distinct():
    assert cr.MEMORY_HEADING != cr.KNOWLEDGE_HEADING
    body = _routed().render()
    assert body.count(cr.MEMORY_HEADING) == 1
    assert body.count(cr.KNOWLEDGE_HEADING) == 1


def test_knowledge_renders_as_pointer_never_body():
    routed = _routed(
        knowledge=[
            {
                "id": "k9",
                "title": "Runbook",
                "summary": "How to deploy.",
                "body": "SECRET BODY",
            }
        ]
    )
    body = routed.render()
    assert "Runbook" in body
    assert "SECRET BODY" not in body
    assert "GET /api/knowledge/items/k9/content" in body
    assert all("body" not in k for k in routed.to_dict()["knowledge"])


def test_to_dict_includes_rendered_text_and_scalar_tiers():
    d = _routed().to_dict()
    assert d["project_id"] == "p1"
    assert d["text"] == _routed().render()
    assert d["knowledge"][0].keys() == {"id", "title", "summary"}


def test_unloaded_catalog_is_honest_about_caps():
    capped = cr.assemble(
        project_id="p", project_name="P", mem_capped=True, know_capped=True
    )
    joined = "\n".join(capped.unloaded)
    assert "more exist" in joined
    assert "memory_recall(query)" in joined
    assert "GET /api/knowledge/items?q=" in joined
    assert "skill_search(query)" in joined
    assert "GET /api/manifest" in joined


def test_empty_tiers_render_placeholders_not_crash():
    routed = cr.assemble(project_id="p", project_name="Empty")
    body = routed.render()
    assert "No project brief or instructions set." in body
    assert "No relevant memories surfaced" in body
    assert "No knowledge items matched." in body


def test_route_context_queries_stores_and_defaults_query_to_project():
    project = SimpleNamespace(
        id="p1",
        name="Website",
        brief="marketing site",
        agent_instructions_template="be terse",
    )
    mem = MagicMock()
    mem.recall_with_provenance.return_value = [
        {"text": "m", "source": "s", "created_at": "2026-07-01"}
    ]
    know = MagicMock()
    know.search.return_value = [{"id": "k1", "title": "Guide", "summary": "s"}]

    routed = cr.route_context(
        project,
        memory_svc=mem,
        knowledge_retriever=know,
        skills=[{"key": "a", "description": "d"}],
    )

    assert "Website" in mem.recall_with_provenance.call_args.kwargs["query_text"]
    assert routed.memories and routed.knowledge and routed.skills
    assert routed.rules.startswith("marketing site")


def test_route_context_degrades_when_a_store_raises():
    project = SimpleNamespace(
        id="p", name="P", brief="", agent_instructions_template=""
    )
    mem = MagicMock()
    mem.recall_with_provenance.side_effect = RuntimeError("vector store down")
    routed = cr.route_context(
        project, query="anything", memory_svc=mem, knowledge_retriever=None, skills=None
    )
    assert routed.memories == []
    assert routed.knowledge == []


def test_route_context_caps_skills_and_flags_the_cap():
    project = SimpleNamespace(
        id="p", name="P", brief="x", agent_instructions_template=""
    )
    skills = [{"key": f"s{i}", "description": "d"} for i in range(cr.SKILL_LIMIT + 5)]
    routed = cr.route_context(
        project, query="q", skills=skills, memory_svc=None, knowledge_retriever=None
    )
    assert len(routed.skills) == cr.SKILL_LIMIT
    assert any("Skills:" in n for n in routed.unloaded)


def test_route_context_scores_skills_and_reports_actual_overflow():
    project = SimpleNamespace(
        id="p", name="P", brief="", agent_instructions_template=""
    )
    routed = cr.route_context(
        project,
        query="deploy",
        skills=[
            {"key": "unrelated", "description": "Write prose."},
            {"key": "release", "description": "Deploy applications."},
            {"key": "docker", "description": "Deploy containers."},
        ],
        memory_svc=None,
        knowledge_retriever=None,
        skill_limit=1,
    )

    assert [skill["key"] for skill in routed.skills] == ["docker"]
    assert routed.skill_overflow == 2
    assert routed.to_dict()["skill_overflow"] == 2
    assert "2 more available" in "\n".join(routed.unloaded)


def test_apply_block_first_write_appends_after_user_content():
    existing = "# My project notes\n\nHand-written stuff.\n"
    block = cr.render_block(_routed())
    out = cr.apply_block(existing, block)
    assert out.startswith("# My project notes")
    assert "Hand-written stuff." in out
    assert cr.GIDEON_START in out and cr.GIDEON_END in out
    assert out.index("Hand-written stuff.") < out.index(cr.GIDEON_START)


def test_apply_block_first_write_into_empty_file():
    out = cr.apply_block("", cr.render_block(_routed()))
    assert out.startswith(cr.GIDEON_START)


def test_apply_block_is_idempotent():
    block = cr.render_block(_routed())
    once = cr.apply_block("preamble\n", block)
    twice = cr.apply_block(once, block)
    assert once == twice
    assert twice.count(cr.GIDEON_START) == 1


def test_apply_block_replaces_only_inside_fence():
    existing = f"BEFORE\n{cr.GIDEON_START}\nstale\n{cr.GIDEON_END}\nAFTER\n"
    out = cr.apply_block(existing, cr.render_block(_routed()))
    assert out.startswith("BEFORE\n")
    assert out.rstrip().endswith("AFTER")
    assert "stale" not in out
    assert "Website" in out


def test_apply_block_malformed_fence_raises():
    with pytest.raises(ValueError, match="exactly one"):
        cr.apply_block(
            f"{cr.GIDEON_START}\na lone start, no end\n", cr.render_block(_routed())
        )
    with pytest.raises(ValueError, match="END precedes START"):
        cr.apply_block(
            f"{cr.GIDEON_END}\nx\n{cr.GIDEON_START}\n", cr.render_block(_routed())
        )


def _count_marker_lines(text: str, marker: str) -> int:
    """Whole-line marker occurrences — the thing apply_block actually keys on."""
    return sum(1 for line in text.splitlines() if line.strip() == marker)


def test_apply_block_ignores_markers_inside_a_code_fence():
    existing = (
        "# Notes\n\n"
        "```\n"
        f"{cr.GIDEON_START}\nexample of the managed block\n{cr.GIDEON_END}\n"
        "```\n"
    )
    assert _count_marker_lines(existing, cr.GIDEON_START) == 1
    once = cr.apply_block(existing, cr.render_block(_routed()))
    twice = cr.apply_block(once, cr.render_block(_routed()))
    assert once == twice
    assert "example of the managed block" in twice
    assert "```" in twice
    assert twice.count(cr.GIDEON_START) == 2


def test_apply_block_refuses_more_than_one_real_block():
    two = f"{cr.GIDEON_START}\na\n{cr.GIDEON_END}\n\n{cr.GIDEON_START}\nb\n{cr.GIDEON_END}\n"
    with pytest.raises(ValueError, match="more than one"):
        cr.apply_block(two, cr.render_block(_routed()))


def test_apply_block_refuses_an_extra_unbalanced_marker():
    nested = f"{cr.GIDEON_START}\nbody\n{cr.GIDEON_END}\ntail\n{cr.GIDEON_END}\n"
    with pytest.raises(ValueError, match="more than one"):
        cr.apply_block(nested, cr.render_block(_routed()))


def test_apply_block_treats_a_mid_line_marker_as_content():
    existing = f"see the {cr.GIDEON_START} marker inline\n"
    out = cr.apply_block(existing, cr.render_block(_routed()))
    assert out.startswith(f"see the {cr.GIDEON_START} marker inline")
    assert "Website" in out
    assert out.rstrip().endswith(cr.GIDEON_END)
    assert _count_marker_lines(out, cr.GIDEON_START) == 1
    assert _count_marker_lines(out, cr.GIDEON_END) == 1


def test_apply_block_idempotent_with_marker_bearing_values():
    routed = _routed(
        project_name="Proj <!-- GIDEON:END -->",
        brief=f"one\n{cr.GIDEON_END}\ntwo",
        knowledge=[
            {"id": "k1", "title": f"Guide\n{cr.GIDEON_START}\nx", "summary": "s"}
        ],
    )
    assert _count_marker_lines(routed.rules, cr.GIDEON_END) >= 1
    block = cr.render_block(routed)
    assert _count_marker_lines(block, cr.GIDEON_START) == 1
    assert _count_marker_lines(block, cr.GIDEON_END) == 1
    once = cr.apply_block("user preamble\n", block)
    twice = cr.apply_block(once, block)
    assert once == twice
    assert _count_marker_lines(once, cr.GIDEON_START) == 1
    assert _count_marker_lines(once, cr.GIDEON_END) == 1


def test_render_escapes_markers_in_every_interpolated_value():
    routed = _routed(
        project_name="Proj\n<!-- GIDEON:END -->",
        brief=f"intro\n{cr.GIDEON_END}\noutro",
        instructions=f"do\n{cr.GIDEON_START}\nthis",
        memories=[{"text": f"note\n{cr.GIDEON_END}\nmore", "source": "chat"}],
        skills=[{"key": "gideon", "description": f"d\n{cr.GIDEON_START}"}],
        knowledge=[{"id": "i1", "title": f"T\n{cr.GIDEON_END}\nx", "summary": "s"}],
    )
    assert _count_marker_lines(routed.rules, cr.GIDEON_END) >= 1
    body = routed.render()
    assert _count_marker_lines(body, cr.GIDEON_START) == 0
    assert _count_marker_lines(body, cr.GIDEON_END) == 0
    assert cr._ESCAPED_END in body
    block = cr.render_block(routed)
    assert _count_marker_lines(block, cr.GIDEON_START) == 1
    assert _count_marker_lines(block, cr.GIDEON_END) == 1


def _make_app(state=None) -> web.Application:
    from gideon.interfaces.dashboard.handlers.context import (
        api_context_get,
        api_project_context_regenerate,
    )

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/context", api_context_get)
    app.router.add_post(
        "/api/projects/{project_id}/context-adapters/regenerate",
        api_project_context_regenerate,
    )
    return app


class _Cfg:
    """A stub AppConfig with a togglable adapters gate."""

    def __init__(self, adapters: bool):
        self.legibility = SimpleNamespace(context_adapters=adapters, discover_tips=True)


@pytest.fixture
def _home(tmp_path, monkeypatch):
    """Point the HierarchyStore at tmp_path and stub the routed-context assembly so
    endpoint tests exercise gating + writes, not live-store retrieval."""
    monkeypatch.setattr("gideon.engine.tasks.hierarchy.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "gideon.interfaces.dashboard.handlers.context._route_for_project",
        lambda state, project, query: cr.assemble(
            project_id=getattr(project, "id", ""),
            project_name=getattr(project, "name", ""),
            brief=getattr(project, "brief", ""),
        ),
    )
    return tmp_path


@pytest.fixture
def _sel_stub():
    with patch("gideon.security.sel.sel") as m:
        inst = MagicMock()
        m.return_value = inst
        yield inst


def _new_project(tmp_path, *, workspace_dir=""):
    from gideon.engine.tasks.hierarchy import HierarchyStore

    with patch("gideon.engine.tasks.hierarchy.config_dir", return_value=tmp_path):
        store = HierarchyStore()
        p = store.create_project("Website")
        if workspace_dir:
            store.update_project(p.id, workspace_dir=workspace_dir)
        return store.get_project(p.id)


@pytest.mark.asyncio
async def test_context_get_falls_back_to_personal_default(_home, _sel_stub):
    async with TestClient(TestServer(_make_app(state=None))) as client:
        resp = await client.get("/api/context")
        assert resp.status == 200
        body = await resp.json()
        assert body["project_name"] == "Personal"
        assert "text" in body


@pytest.mark.asyncio
async def test_context_get_honors_explicit_project_id(_home, _sel_stub):
    project = _new_project(_home)
    async with TestClient(TestServer(_make_app(state=None))) as client:
        resp = await client.get(f"/api/context?project_id={project.id}")
        assert resp.status == 200
        assert (await resp.json())["project_name"] == "Website"


@pytest.mark.asyncio
async def test_regenerate_404_for_unknown_project(_home, _sel_stub):
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.post("/api/projects/nope/context-adapters/regenerate")
        assert resp.status == 404


@pytest.mark.asyncio
async def test_regenerate_403_when_adapters_disabled(_home, _sel_stub, monkeypatch):
    project = _new_project(_home, workspace_dir=str(_home))
    monkeypatch.setattr(
        "gideon.core.config.loader.AppConfig.load", classmethod(lambda cls: _Cfg(False))
    )
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.post(
            f"/api/projects/{project.id}/context-adapters/regenerate"
        )
        assert resp.status == 403
        assert "disabled" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_regenerate_400_when_no_workspace_bound(_home, _sel_stub, monkeypatch):
    project = _new_project(_home)
    monkeypatch.setattr(
        "gideon.core.config.loader.AppConfig.load", classmethod(lambda cls: _Cfg(True))
    )
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.post(
            f"/api/projects/{project.id}/context-adapters/regenerate"
        )
        assert resp.status == 400
        assert "workspace" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_regenerate_writes_fenced_block_and_audits(
    _home, _sel_stub, monkeypatch, tmp_path
):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "CLAUDE.md").write_text("# Mine\n\nkeep me\n", encoding="utf-8")
    project = _new_project(_home, workspace_dir=str(ws))
    monkeypatch.setattr(
        "gideon.core.config.loader.AppConfig.load", classmethod(lambda cls: _Cfg(True))
    )

    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.post(
            f"/api/projects/{project.id}/context-adapters/regenerate"
        )
        assert resp.status == 200
        out = await resp.json()
        assert out["ok"] is True
        assert not out["errors"]
        assert len(out["written"]) == len(
            __import__(
                "gideon.interfaces.dashboard.handlers.context",
                fromlist=["ADAPTER_FILES"],
            ).ADAPTER_FILES
        )

    claude = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert "keep me" in claude
    assert cr.GIDEON_START in claude and cr.GIDEON_END in claude
    assert (ws / "AGENTS.md").exists() and (ws / ".cursorrules").exists()
    assert _sel_stub.log_api_access.called


@pytest.mark.asyncio
async def test_regenerate_400_when_workspace_is_unsafe(_home, _sel_stub, monkeypatch):
    from gideon.engine.tasks.hierarchy import HierarchyStore

    store = HierarchyStore()
    p = store.create_project("Website")
    proj = store.get_project(p.id)
    proj.workspace_dir = "/"
    store._write_project(proj)
    monkeypatch.setattr(
        "gideon.core.config.loader.AppConfig.load", classmethod(lambda cls: _Cfg(True))
    )
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.post(f"/api/projects/{p.id}/context-adapters/regenerate")
        assert resp.status == 400
        body = await resp.json()
        assert body["error"]["code"] == "workspace_dir_unsafe"
    assert not (pathlib.Path("/") / "CLAUDE.md").exists()


@pytest.mark.asyncio
async def test_regenerate_writes_through_atomic_write(
    _home, _sel_stub, monkeypatch, tmp_path
):
    import gideon.interfaces.dashboard.handlers.context as ctx_mod

    ws = tmp_path / "ws"
    ws.mkdir()
    project = _new_project(_home, workspace_dir=str(ws))
    monkeypatch.setattr(
        "gideon.core.config.loader.AppConfig.load", classmethod(lambda cls: _Cfg(True))
    )
    calls: list[str] = []
    real_atomic = ctx_mod.atomic_write

    def _spy(path, content, **kw):
        calls.append(str(path))
        return real_atomic(path, content, **kw)

    monkeypatch.setattr(ctx_mod, "atomic_write", _spy)
    async with TestClient(TestServer(_make_app())) as client:
        resp = await client.post(
            f"/api/projects/{project.id}/context-adapters/regenerate"
        )
        assert resp.status == 200
    assert len(calls) == len(ctx_mod.ADAPTER_FILES)
    assert all((ws / name).exists() for name in ctx_mod.ADAPTER_FILES)
