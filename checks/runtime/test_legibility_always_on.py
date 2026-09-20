"""PEP-10 — the always-on conventions viewer, and the three domain-craft skills.

The clause that decides whether this atom is real is *"the viewer matches what a session
actually receives"*. A viewer that renders its own idea of the conventions is worse than no
viewer: it drifts silently while the user trusts it. So the load-bearing test in this file is
:func:`test_viewer_items_appear_in_a_really_assembled_session_prompt` — it assembles a REAL
session prompt through ``PromptAssembler.build_session_context`` (the composer every session runs
through) and asserts every item the viewer reports is a substring of it.

Every comparison here carries a **vacuity assertion**. An empty viewer matching an empty prompt
passes forever, and on a fresh home the always-on skill tier is genuinely empty — no bundled
skill ships ``always: true`` — so this rail would be vacuous by default. Each test therefore
plants real content and asserts the planted content was non-trivial before comparing.
"""

from __future__ import annotations

import resource
import signal
from unittest.mock import patch

import pytest

from gideon.assurance.legibility.always_on import (
    InstructionWriteError,
    collect_always_on,
    parse_always_skill_parts,
    read_instruction,
    write_instruction,
)
from gideon.cognition import project_context
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.extensions.skills.loader import ProcedureLibrary

ALWAYS_SKILL_BODY = (
    "# House conventions\n\n"
    "PEP10_ALWAYS_MARKER — every session in this house follows these rules:\n\n"
    "- Never rewrite history in a ledger.\n"
    "- Name what you did not check.\n"
)
OVERVIEW_BODY = (
    "PEP10_OVERVIEW_MARKER — the roof deck is stripped and the membrane is on order. "
    "Next: flashing details before the first rain."
)
MIN_MEANINGFUL_BODY = 40


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """One explicit home for every test in this file.

    Set for the WHOLE file rather than per-test: mixing a set and an unset
    ``GIDEON_HOME`` across tests in one module resolves two different homes through the
    conftest passthrough rule and produces failures that read as product bugs.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    return home


@pytest.fixture()
def store(isolated_home):
    """A HierarchyStore rooted in the isolated home.

    ``tasks.hierarchy`` binds ``config_dir`` at import, so the env var alone is not enough to
    guarantee it for every resolution path; patch the bound name too.
    """
    with patch("gideon.engine.tasks.hierarchy.config_dir", return_value=isolated_home):
        yield HierarchyStore()


def _plant_always_skill(home, name: str = "house-conventions") -> None:
    """Opt a skill into the always-on tier the only way the product supports."""
    skill_dir = home / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: House rules.\nalways: true\n---\n\n"
        f"{ALWAYS_SKILL_BODY}",
        encoding="utf-8",
    )


def _assembled_session_text(project_id: str = "") -> str:
    """A really-assembled session prompt — the text a session actually receives.

    ``PromptAssembler.build_session_context`` is the single composer every session runs through
    (``context.build_message`` calls it; the gateway, the context engine and subagents all call
    ``build_message``). The project block is composed by ``chat_utils._project_context_preamble``,
    which ``chat_runner`` calls for a project-bound chat. Both are concatenated into the turn, so
    both are concatenated here.
    """
    from gideon.cognition.context import PromptAssembler
    from gideon.interfaces.dashboard.chat_utils import _project_context_preamble

    parts = [PromptAssembler().build_session_context(session_key=None, agent="gideon")]
    if project_id:
        parts.append(_project_context_preamble(project_id))
    return "\n".join(parts)


def test_viewer_items_appear_in_a_really_assembled_session_prompt(isolated_home, store):
    """Every item the viewer reports must be present in a REAL assembled session prompt.

    This is the atom's "spot-checked against an assembled prompt". It fails if the viewer and
    the session composer ever diverge — if the viewer starts reporting something a session does
    not receive, or reports it with different text.
    """
    _plant_always_skill(isolated_home)
    project = store.create_project("Roofing Rebuild", brief="Rebuild the roof deck.")
    assert project_context.write_overview(project.id, OVERVIEW_BODY)

    inventory = collect_always_on(project_id=project.id)
    assembled = _assembled_session_text(project.id)

    assert len(assembled) > 500, "assembled prompt is too small to be a real prompt"
    skills = [i for i in inventory.items if i.kind == "always_skill"]
    instructions = [i for i in inventory.items if i.kind == "project_instruction"]
    assert (
        skills
    ), "no always-on skill in the inventory — the comparison would be vacuous"
    assert (
        instructions
    ), "no project instruction in the inventory — the comparison is vacuous"

    for item in inventory.items:
        assert (
            len(item.body) >= MIN_MEANINGFUL_BODY
        ), f"{item.id} body is {len(item.body)} chars — too short to be evidence of agreement"
        assert item.body in assembled, (
            f"the viewer reports {item.id} but the assembled session prompt does not contain "
            f"its body — viewer and session composer have DIVERGED"
        )


def test_viewer_does_not_report_on_demand_skills_as_always_on(isolated_home):
    """On-demand skills are indexed, not injected — reporting them would misstate the prompt."""
    _plant_always_skill(isolated_home)
    inventory = collect_always_on()
    names = {i.name for i in inventory.items}

    assert "house-conventions" in names, "vacuity: the always-on tier is empty"
    for on_demand in ("web-verify", "document-authoring", "research-campaign"):
        assert (
            on_demand not in names
        ), f"{on_demand} is on-demand but reported as always-on"
    assert "## Available Skills" not in "".join(i.body for i in inventory.items)


def test_fresh_home_reports_an_empty_always_skill_tier_and_names_the_mechanism(
    isolated_home,
):
    """No bundled skill ships always:true, so the tier is legitimately empty on a fresh home.

    The viewer must say how to populate it rather than render a blank panel — an empty surface
    that does not name its own mechanism reads as a broken page.
    """
    payload = collect_always_on().to_dict()
    assert payload["counts"]["always_skills"] == 0
    assert "always: true" in payload["always_skill_mechanism"]
    always = [s for s in ProcedureLibrary().list_skills() if s["always"]]
    assert always == [], f"a bundled skill now ships always:true: {always}"


def test_provenance_distinguishes_global_from_project(isolated_home, store):
    _plant_always_skill(isolated_home)
    project = store.create_project("Roofing Rebuild")
    assert project_context.write_overview(project.id, OVERVIEW_BODY)

    inventory = collect_always_on(project_id=project.id)
    scopes = {i.name: i.scope for i in inventory.items}
    assert scopes["house-conventions"] == "global"
    assert scopes[project_context.OVERVIEW_FILE] == "project"
    sources = {i.name: i.source for i in inventory.items}
    assert sources["house-conventions"] == "user"
    assert sources[project_context.OVERVIEW_FILE] == f"project:{project.id}"


def test_list_preview_is_redacted_but_the_editor_body_is_verbatim(isolated_home, store):
    """Redacted metadata, verbatim round-trip — redacting the editor body would save the
    redaction over the user's real text."""
    project = store.create_project("Roofing Rebuild")
    secret = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    assert project_context.write_overview(
        project.id, f"{OVERVIEW_BODY}\ntoken: {secret}"
    )

    item = collect_always_on(project_id=project.id).by_id(
        f"project_instruction:{project_context.OVERVIEW_FILE}"
    )
    assert item is not None
    listed = item.to_dict()
    assert secret not in listed["preview"], "a credential leaked into the list preview"
    assert "preview" in listed and "body" not in listed
    verbatim = item.to_dict(include_body=True)
    assert secret in verbatim["body"], "the editor round-trip must not redact the body"


def test_editing_a_project_instruction_round_trips_into_a_session(isolated_home, store):
    """Write, read back, and confirm a session then receives the EDITED text.

    Reading back through the viewer is not enough — that only proves the viewer is
    self-consistent. The edit has to show up in the assembled prompt.
    """
    project = store.create_project("Roofing Rebuild")
    assert project_context.write_overview(project.id, OVERVIEW_BODY)
    item_id = f"project_instruction:{project_context.OVERVIEW_FILE}"

    edited = "PEP10_EDITED_MARKER — the membrane arrived; flashing starts Monday."
    assert len(edited) >= MIN_MEANINGFUL_BODY
    written = write_instruction(item_id, edited, project_id=project.id)
    assert written.body == edited

    assert read_instruction(item_id, project_id=project.id).body == edited
    assert project_context.read_overview(project.id) == edited

    assembled = _assembled_session_text(project.id)
    assert len(assembled) > 500
    assert edited in assembled
    assert "PEP10_OVERVIEW_MARKER" not in assembled


def test_a_failed_write_raises_and_leaves_the_previous_text_intact(
    isolated_home, store
):
    """A failed write must NOT report success — the store reports failure as a bare ``False``.

    Measured behaviour: ``project_context.write_overview`` swallows its own ``OSError`` and
    returns ``False``, and because the write is atomic the previous content survives. A caller
    that ignored the ``False`` would render "Saved" over an edit that was silently discarded.

    The failure is injected with the OS file-size limit rather than directory permission bits:
    the suite runs as root, for whom ``chmod`` read-only is advisory, so the permission route
    was vacuous. ``SIGXFSZ`` is ignored so the kernel reports ``EFBIG`` from the write instead
    of terminating the test process.
    """
    project = store.create_project("Roofing Rebuild")
    assert project_context.write_overview(project.id, OVERVIEW_BODY)
    item_id = f"project_instruction:{project_context.OVERVIEW_FILE}"

    previous_handler = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    original_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (4, original_limit[1]))
    try:
        with pytest.raises(InstructionWriteError) as excinfo:
            write_instruction(item_id, "SHOULD_NOT_LAND", project_id=project.id)
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, original_limit)
        signal.signal(signal.SIGXFSZ, previous_handler)

    assert excinfo.value.status == 500
    assert "NOT saved" in excinfo.value.reason
    assert project_context.read_overview(project.id) == OVERVIEW_BODY
    assert "SHOULD_NOT_LAND" not in _assembled_session_text(project.id)


def test_a_ledger_is_not_editable_through_the_viewer(isolated_home, store):
    """Ledgers are append-only history. Rewriting them through a viewer would destroy the one
    tier whose value is that it is not rewritten."""
    project = store.create_project("Roofing Rebuild")
    project_context.append_ledger(
        project.id, "decisions", "Chose a standing-seam roof."
    )

    inventory = collect_always_on(project_id=project.id)
    ledger = inventory.by_id("project_instruction:decisions.md")
    assert (
        ledger is not None
    ), "vacuity: the ledger was not inlined, so nothing was checked"
    assert ledger.editable is False
    assert ledger.read_only_reason

    with pytest.raises(InstructionWriteError) as excinfo:
        write_instruction(
            "project_instruction:decisions.md", "rewritten", project_id=project.id
        )
    assert excinfo.value.status == 403
    assert project_context.read_ledger(project.id, "decisions") == [
        "Chose a standing-seam roof."
    ]


def test_write_refuses_a_symlinked_instruction_leaf(isolated_home, store, tmp_path):
    """Symlink-leaf rejection — a symlinked leaf would let an edit land outside the trust base."""
    project = store.create_project("Roofing Rebuild")
    context_dir = project_context._context_dir(project.id)
    assert context_dir is not None
    context_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.md"
    outside.write_text("ORIGINAL_OUTSIDE", encoding="utf-8")
    (context_dir / project_context.OVERVIEW_FILE).symlink_to(outside)

    with pytest.raises(InstructionWriteError) as excinfo:
        write_instruction(
            f"project_instruction:{project_context.OVERVIEW_FILE}",
            "should not escape",
            project_id=project.id,
        )
    assert excinfo.value.status == 403
    assert outside.read_text(encoding="utf-8") == "ORIGINAL_OUTSIDE"


def test_write_refuses_an_unknown_project_and_a_missing_project_id(
    isolated_home, store
):
    item_id = f"project_instruction:{project_context.OVERVIEW_FILE}"
    with pytest.raises(InstructionWriteError) as no_pid:
        write_instruction(item_id, "x", project_id="")
    assert no_pid.value.status == 400
    with pytest.raises(InstructionWriteError) as unknown:
        write_instruction(item_id, "x", project_id="p-does-not-exist")
    assert unknown.value.status == 404


def test_read_instruction_404s_for_an_item_not_in_effect(isolated_home):
    with pytest.raises(InstructionWriteError) as excinfo:
        read_instruction("always_skill:not-a-skill", project_id="")
    assert excinfo.value.status == 404


def test_parser_agrees_with_the_producer_on_a_live_loader(isolated_home):
    """The parse target is ``ProcedureLibrary.get_context`` output, not a fixture of it."""
    _plant_always_skill(isolated_home)
    context_text = ProcedureLibrary().get_context()
    assert context_text, "vacuity: the producer emitted nothing"
    parsed = parse_always_skill_parts(context_text)
    assert [name for name, _ in parsed] == ["house-conventions"]
    body = parsed[0][1]
    assert "PEP10_ALWAYS_MARKER" in body
    assert (
        body in context_text
    ), "the parsed body is not a verbatim slice of the producer"
    assert "## Available Skills" not in body


def test_parser_keeps_a_skill_bodys_own_horizontal_rule():
    """A body containing ``---`` must not be truncated at its own markdown rule."""
    text = (
        "[Skills:]\n### Skill: alpha\n\nfirst\n\n---\n\nsecond\n\n---\n\n"
        "### Skill: beta\n\nbee\n\n---\n\n## Available Skills\n\n- **x**: y\n"
        "[End of skills]\n\n"
    )
    parsed = dict(parse_always_skill_parts(text))
    assert set(parsed) == {"alpha", "beta"}
    assert "second" in parsed["alpha"], "the body was cut at its own horizontal rule"
    assert parsed["beta"] == "bee"


def test_parser_is_empty_on_empty_input():
    assert parse_always_skill_parts("") == []
    assert parse_always_skill_parts("   ") == []


NEW_SKILLS = ("web-verify", "document-authoring", "research-campaign")

SURFACING_CASES = {
    "web-verify": (
        "Can you verify the page renders correctly in the browser?",
        [
            "Please verify email for the new user account.",
            "What is the capital of France?",
        ],
    ),
    "document-authoring": (
        "Please draft a report for the eng leads about test flakiness.",
        [
            "Write a commit message for this diff.",
            "What is the capital of France?",
        ],
    ),
    "research-campaign": (
        "Do research on whether we should migrate, and run a research campaign.",
        [
            "Just tell me the current disk usage.",
            "What is the capital of France?",
        ],
    ),
}


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_bundled_skill_loads_with_the_frontmatter_contract(isolated_home, name):
    loader = ProcedureLibrary()
    rows = {s["name"]: s for s in loader.list_skills()}
    assert name in rows, f"{name} did not load"
    row = rows[name]
    assert row["description"] and row["description"] != name
    assert row["triggers"], "no triggers means the skill can never surface"
    assert (
        row["always"] is False
    ), "a domain-craft skill is loaded when relevant, not always"
    body = loader.load_skill(name) or ""
    assert len(body) > 800, f"{name} is too thin to be a worked skill"
    assert "## Worked example" in body or "## Worked example" in body.replace(
        "###", "##"
    )


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_bundled_skill_surfaces_when_relevant(isolated_home, name):
    relevant, _ = SURFACING_CASES[name]
    surfaced = ProcedureLibrary().get_triggered_skills(relevant)
    assert name in surfaced, f"{name} did not surface for {relevant!r}; got {surfaced}"


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_bundled_skill_stays_quiet_when_irrelevant(isolated_home, name):
    _, irrelevant_texts = SURFACING_CASES[name]
    loader = ProcedureLibrary()
    for text in irrelevant_texts:
        surfaced = loader.get_triggered_skills(text)
        assert (
            name not in surfaced
        ), f"{name} surfaced for the irrelevant {text!r}: {surfaced}"


NEGATIVE_TRIGGER_CASES = {
    "web-verify": (
        "Verify email delivery, then check the site.",
        "Then check the site.",
    ),
    "document-authoring": (
        "Write a commit message for this document change.",
        "Write a document change.",
    ),
    "research-campaign": ("Just tell me the research plan.", "Give the research plan."),
}


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_a_negative_trigger_vetoes_a_positive_match(isolated_home, name):
    """The negative triggers must do real work, not decorate the frontmatter."""
    suppressed, control = NEGATIVE_TRIGGER_CASES[name]
    loader = ProcedureLibrary()

    assert name in loader.get_triggered_skills(
        control
    ), f"the control {control!r} does not surface {name}, so the suppression below proves nothing"
    assert name not in loader.get_triggered_skills(
        suppressed
    ), f"{name} surfaced for {suppressed!r} — its negative trigger did not veto a positive match"


def test_new_skills_are_present_in_a_sessions_on_demand_index(isolated_home):
    """Surfacing is per-turn, but the skill must also be discoverable in every session's index —
    otherwise an agent only finds it by accident."""
    context_text = ProcedureLibrary().get_context()
    assert "## Available Skills" in context_text
    for name in NEW_SKILLS:
        assert (
            f"**{name}**" in context_text
        ), f"{name} is missing from the session skill index"


_NO_BODY = object()


def _call(
    handler,
    *,
    method: str = "GET",
    query: dict | None = None,
    body: object = _NO_BODY,
    raw: bytes | None = None,
) -> tuple[int, dict]:
    """Drive a handler with a real ``aiohttp`` request.

    The query string goes in the path (``make_mocked_request`` parses it into ``request.query``)
    and the body as serialized bytes with a JSON content type, so the handler reads through the
    real ``gideon.core.http_request`` boundary instead of a request-shaped fake.
    """
    import asyncio
    import json
    from urllib.parse import urlencode

    from aiohttp.test_utils import make_mocked_request

    path = "/api/legibility/always-on"
    if query:
        path += "?" + urlencode(query)
    headers: dict[str, str] = {}
    payload = b""
    if raw is not None:
        payload, headers["Content-Type"] = raw, "application/json"
    elif body is not _NO_BODY:
        payload, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    request = make_mocked_request(method, path, headers=headers)
    request._read_bytes = payload
    resp = asyncio.run(handler(request))
    return resp.status, json.loads(resp.body.decode())


def test_the_three_viewer_routes_are_registered_to_their_handlers():
    """Registration is asserted at source because these routes are wired inside
    ``start_dashboard``, which cannot be invoked in a unit test. A handler with no route is the
    inert-control shape this repo keeps finding."""
    import inspect

    from gideon.interfaces.dashboard import server

    source = inspect.getsource(server.start_dashboard)
    for line in (
        'app.router.add_get("/api/legibility/always-on", api_always_on)',
        'app.router.add_get("/api/legibility/always-on/doc", api_always_on_doc)',
        'app.router.add_put("/api/legibility/always-on/doc", api_always_on_doc_write)',
    ):
        assert line in source, f"route not registered: {line}"
    from gideon.interfaces.dashboard.handlers import legibility as handlers

    for name in ("api_always_on", "api_always_on_doc", "api_always_on_doc_write"):
        assert callable(getattr(handlers, name, None)), f"handler missing: {name}"


def test_get_always_on_returns_the_inventory(isolated_home, store):
    from gideon.interfaces.dashboard.handlers.legibility import api_always_on

    _plant_always_skill(isolated_home)
    project = store.create_project("Roofing Rebuild")
    assert project_context.write_overview(project.id, OVERVIEW_BODY)

    status, payload = _call(api_always_on, query={"project_id": project.id})
    assert status == 200
    assert payload["counts"]["always_skills"] == 1
    assert payload["counts"]["project_instructions"] == 1
    ids = {row["id"] for row in payload["items"]}
    assert ids == {"always_skill:house-conventions", "project_instruction:overview.md"}


def test_doc_get_and_put_round_trip_over_http(isolated_home, store):
    from gideon.interfaces.dashboard.handlers.legibility import (
        api_always_on_doc,
        api_always_on_doc_write,
    )

    project = store.create_project("Roofing Rebuild")
    assert project_context.write_overview(project.id, OVERVIEW_BODY)
    item_id = f"project_instruction:{project_context.OVERVIEW_FILE}"

    status, payload = _call(
        api_always_on_doc, query={"id": item_id, "project_id": project.id}
    )
    assert status == 200 and payload["body"] == OVERVIEW_BODY

    edited = "PEP10_HTTP_EDIT_MARKER — the flashing is done and the scaffold comes down Friday."
    status, payload = _call(
        api_always_on_doc_write,
        method="PUT",
        body={"id": item_id, "project_id": project.id, "body": edited},
    )
    assert status == 200 and payload["ok"] is True
    assert payload["item"]["body"] == edited
    assert edited in _assembled_session_text(project.id)


def test_doc_put_surfaces_a_refusal_as_an_error_not_a_silent_success(
    isolated_home, store
):
    """403 with a reason, not ``{"ok": true}`` — the whole point of the write guard."""
    from gideon.interfaces.dashboard.handlers.legibility import api_always_on_doc_write

    project = store.create_project("Roofing Rebuild")
    project_context.append_ledger(
        project.id, "decisions", "Chose a standing-seam roof."
    )
    status, payload = _call(
        api_always_on_doc_write,
        method="PUT",
        body={
            "id": "project_instruction:decisions.md",
            "project_id": project.id,
            "body": "rewritten",
        },
    )
    assert status == 403
    assert "ok" not in payload and payload["error"]
    assert project_context.read_ledger(project.id, "decisions") == [
        "Chose a standing-seam roof."
    ]


@pytest.mark.parametrize(
    "body,expected",
    [
        (None, "Body must be a JSON object"),
        ({}, "id is required"),
        ({"id": "x"}, "body is required"),
        ({"id": "x", "body": 3}, "body must be a string"),
    ],
)
def test_doc_put_validates_its_payload(isolated_home, body, expected):
    from gideon.interfaces.dashboard.handlers.legibility import api_always_on_doc_write

    status, payload = _call(api_always_on_doc_write, method="PUT", body=body)
    assert status == 400
    assert payload["error"] == expected


def test_doc_put_rejects_a_non_json_body(isolated_home):
    from gideon.interfaces.dashboard.handlers.legibility import api_always_on_doc_write

    status, payload = _call(
        api_always_on_doc_write, method="PUT", raw=b"not json at all"
    )
    assert status == 400 and payload["error"] == "Invalid JSON body"


def test_doc_get_requires_an_id(isolated_home):
    from gideon.interfaces.dashboard.handlers.legibility import api_always_on_doc

    status, payload = _call(api_always_on_doc)
    assert status == 400 and payload["error"] == "id is required"
