"""The rail: a dashboard-writable Slack setting cannot exist with nothing reading it.

Two bugs shipped in this bundle because the Configure form wrote a key that no runtime
path consumed:

* **#952** — ``bot_token`` / ``app_token`` reached the OUTBOUND half from the app store and
  the INBOUND half from core's credential store (``.env`` / keychain / env), which never
  reads the app store. A dashboard-only install printed "connected to Slack", showed a
  green provider row, and never started a receiver.
* **#953** — ``allowed_users`` was parsed into :class:`SlackDeskSettings` and then ignored: the
  runtime seeded its allowlist from ``GIDEON_OWNER_ID`` alone, and the authorization
  gate ignored the set anyway and answered :func:`is_owner`.

The rails below are **derived from the dataclass's own fields** (``dataclasses.fields``)
plus the two credential keys, so they extend themselves when someone adds a setting — no
list to forget to update.

**What each rail proves, measured against pristine pre-fix sources rather than assumed:**

* ``test_schema_and_dataclass_are_symmetric`` is exact in both directions, and is the only
  rail that can see #952 at all — a token is not a ``SlackDeskSettings`` field, so
  ``CREDENTIAL_SETTING_KEYS`` is how the two credential keys enter the derivation.
* ``test_every_settings_field_is_read_outside_settings_py`` **would have flagged
  ``allowed_users``** on the pre-#953 tree: its only mention outside ``settings.py`` was
  ``getattr(orch.settings, "allowed_users", [])`` — a string, not an attribute read — so the
  field had no reader at all. It is still only a FLOOR: it proves a consumer exists, not
  that the consumer's answer changes anything. It does not see #953's second half (the gate
  ignoring the set it was handed) and it passes ``allowed_enterprise_ids`` today, whose
  consumer discards it.
* So the gates themselves are pinned BEHAVIOURALLY below the floor, one test per claim,
  including the fail-closed direction and the two settings that remain deliberately inert.

A new field inherits the floor for free, and needs its own behavioural test if it gates
anything.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import fields
from pathlib import Path

import pytest

import slack_desk_runtime.enterprise as enterprise
import slack_desk_runtime.handler as H
from slack_desk_runtime.runtime import SlackDeskRuntime
from slack_desk_runtime.settings import CREDENTIAL_SETTING_KEYS, SlackDeskSettings, load_tokens, reload_settings
from slack_desk_runtime.transport import SlackDeskTransport

_APP_DIR = Path(__file__).resolve().parents[1]
_SETTINGS_FIELDS = tuple(f.name for f in fields(SlackDeskSettings))


def _schema_properties() -> dict:
    manifest = json.loads((_APP_DIR / "app.json").read_text(encoding="utf-8"))
    return manifest["provider"]["settingsSchema"]["properties"]


# ── the derived rails ───────────────────────────────────────────────────────


def test_schema_and_dataclass_are_symmetric():
    """Every dashboard-writable key is modelled, and every modelled key is writable.

    A key in the schema and not in the model is a control with nothing behind it (the
    #952/#953 shape). A key in the model and not in the schema is a setting the operator
    can never reach from the dashboard.
    """
    declared = set(_schema_properties())
    modelled = set(_SETTINGS_FIELDS) | set(CREDENTIAL_SETTING_KEYS)
    assert declared - modelled == set(), (
        "app.json exposes settings the app does not model — a dashboard control with "
        "nothing behind it. Add them to SlackDeskSettings (behaviour) or to "
        f"CREDENTIAL_SETTING_KEYS (credentials): {sorted(declared - modelled)}"
    )
    assert modelled - declared == set(), (
        "the app models settings app.json cannot write — unreachable from the dashboard: "
        f"{sorted(modelled - declared)}"
    )


def _runtime_sources() -> dict[str, str]:
    """Every app source that is neither ``settings.py`` nor a test, by path.

    ``test_*.py`` is excluded by NAME as well as by directory: this bundle keeps
    ``test_provider.py`` / ``test_inbox_source.py`` at the app root (the app-bar contract
    wants them there), and a test reading a field proves nothing about the runtime.
    """
    return {
        str(p.relative_to(_APP_DIR)): p.read_text(encoding="utf-8")
        for p in sorted(_APP_DIR.rglob("*.py"))
        if "__pycache__" not in p.parts
        and "tests" not in p.parts
        and not p.name.startswith("test_")
        and p.name != "settings.py"
    }


def _settings_accessors() -> dict[str, str]:
    """``{method: source}`` for ``SlackDeskSettings``' own public methods, minus the parser.

    Derived from the class, so a new accessor is picked up automatically. ``load`` is
    excluded: parsing the store into the dataclass is not consuming a field.
    """
    out: dict[str, str] = {}
    for name, member in vars(SlackDeskSettings).items():
        if name.startswith("_") or name == "load":
            continue
        func = member.__func__ if isinstance(member, (classmethod, staticmethod)) else member
        if callable(func):
            out[name] = inspect.getsource(func)
    return out


@pytest.mark.parametrize("field_name", _SETTINGS_FIELDS)
def test_every_settings_field_is_read_outside_settings_py(field_name):
    """A ``SlackDeskSettings`` field must be consumed somewhere other than where it is parsed.

    Counted two ways, both derived: a direct ``.<field>`` read in a runtime module, or a
    ``SlackDeskSettings`` accessor that reads ``self.<field>`` and is itself called from a
    runtime module (``allowed_enterprise_ids`` → ``enterprise_ids()``, ``dm_activation`` →
    ``channel_config()`` — legitimate derived reads, not dead settings).

    This is the FLOOR, not the ceiling — see the module docstring. It proves a consumer
    exists, not that the consumer's answer changes anything: ``allowed_enterprise_ids``
    passes it today while ``validate_enterprise`` documents the argument as ignored. It DID
    have the reach to flag ``allowed_users`` before #953 (measured on the pre-fix tree: the
    field had no attribute read outside ``settings.py`` at all), but not the reach to see
    the gate ignoring the set. Both are pinned behaviourally further down.
    """
    sources = _runtime_sources()
    direct = sorted(p for p, src in sources.items() if f".{field_name}" in src)
    via: list[str] = []
    for accessor, accessor_src in _settings_accessors().items():
        if f"self.{field_name}" not in accessor_src:
            continue
        via += [f"{p} → .{accessor}()" for p, src in sources.items() if f".{accessor}(" in src]
    assert direct or via, (
        f"SlackSettings.{field_name} is writable from the dashboard (app.json declares it) "
        "and no runtime module reads it, directly or through an accessor. Wire it in or "
        "drop the dashboard control — a setting that persists and changes nothing is the "
        "#952/#953 defect."
    )


# ── #952: the credential keys must reach BOTH halves of the channel ─────────


@pytest.fixture
def store_only_home(tmp_path, monkeypatch):
    """An isolated home whose app store is the ONLY place the tokens exist."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    for key in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "GIDEON_OWNER_ID"):
        monkeypatch.delenv(key, raising=False)
    cfg_path = tmp_path / "apps" / "gideonai-slack-desk" / "data" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    return cfg_path


def _write_store(cfg_path: Path, **values) -> dict:
    cfg_path.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    reload_settings()
    return values


class _Services:
    """GatewayServices stand-in: the config handle + owner_id, no live services."""

    sessions = ctx_builder = conv_log = consolidator = None
    subagent_mgr = channel_history = dashboard_state = None

    def __init__(self, owner_id: str = "") -> None:
        from gideon.sdk.channel import AppConfig

        self._cfg = AppConfig.load()
        self._owner = owner_id

    @property
    def config(self):
        return self._cfg

    @property
    def owner_id(self) -> str:
        return self._owner

    async def deliver_channel_inbound(self, provider, msg, *, is_dm=True):
        raise AssertionError("not driven by this test")


@pytest.mark.parametrize("key", CREDENTIAL_SETTING_KEYS)
def test_a_credential_setting_reaches_the_runtime_from_the_store_alone(key, store_only_home):
    """Each token key, present ONLY in the app store, must reach the inbound runtime.

    Parametrized over the credential keys rather than spelled out, so a third token added
    to the schema is covered without editing this test. Withholding one key at a time is
    what makes the assertion sharp: before #952 the runtime read neither.
    """
    store = {k: f"fake-{k}-value" for k in CREDENTIAL_SETTING_KEYS}
    _write_store(store_only_home, **store)

    resolved = dict(zip(CREDENTIAL_SETTING_KEYS, load_tokens(store), strict=True))
    assert resolved[key] == store[key], (
        f"{key} is in the app store and the shared resolver did not return it — the "
        "inbound half will fall back to the credential store and stay offline (#952)."
    )

    runtime = SlackDeskRuntime(_Services(), config=store)
    assert runtime._slack_desk_enabled, (
        "the store holds both tokens and the runtime reports Slack disabled — inbound "
        "would silently never start (#952)."
    )


@pytest.mark.asyncio
async def test_store_tokens_start_inbound_and_outbound_from_one_source(store_only_home):
    """The transport and the runtime must agree, from the store alone.

    The exact #952 asymmetry: outbound live (``connected``, "connected to Slack") while
    inbound was dead, because the two halves resolved the same two credentials from
    different places.
    """
    store = _write_store(
        store_only_home, bot_token="xoxb-fake-store-bot", app_token="xapp-1-fake-store-app"
    )
    transport = SlackDeskTransport(store)
    assert transport.connected, "outbound did not see the store tokens"

    runtime = SlackDeskRuntime(_Services(), config=transport._config)
    assert runtime._bot_token == store["bot_token"]
    assert runtime._app_token == store["app_token"]
    assert runtime._slack_desk_enabled, "inbound did not see the same tokens outbound just did"


@pytest.mark.asyncio
async def test_start_inbound_hands_the_store_to_the_runtime(store_only_home, monkeypatch):
    """Drive the real ``start_inbound`` wiring, not just the pieces either side of it.

    Added because a mutation run caught the gap: reverting BOTH store paths (the transport
    withholding its config *and* ``load_tokens`` no longer reading the store) left every
    other test in this file green, because they all construct ``SlackDeskRuntime`` directly. The
    seam between the two halves needs its own coverage or the wiring is untested.

    Kept offline by failing workspace validation, which is the earliest network call on the
    path; the runtime is already built and attached by then, which is what this asserts.
    """
    store = _write_store(
        store_only_home, bot_token="xoxb-fake-store-bot", app_token="xapp-1-fake-store-app"
    )
    monkeypatch.setattr("slack_desk_runtime.events.validate_enterprise", lambda *a, **k: False)

    transport = SlackDeskTransport(store)
    await transport.start_inbound(_Services("U_OWNER"))

    assert transport._runtime is not None, (
        "start_inbound bailed at the token gate although the store holds both tokens — the "
        "transport is not handing its config to the runtime (#952)."
    )
    assert transport._runtime._bot_token == store["bot_token"]
    assert transport._runtime._app_token == store["app_token"]
    assert "auth.test" in transport._inbound_offline_reason


@pytest.mark.asyncio
async def test_health_reports_inbound_offline_instead_of_a_green_row(store_only_home):
    """A bot token with no app token is a HALF-configured channel, and must say so.

    #952's operator was misled by two true-but-irrelevant signals. ``health()`` claiming
    "ready — Tokens configured" off the bot token alone was one of them.
    """
    store = _write_store(store_only_home, bot_token="xoxb-fake-store-bot")
    transport = SlackDeskTransport(store)
    await transport.start_inbound(_Services())

    health = await transport.health()
    assert health["state"] == "error", health
    assert "App Token" in health["detail"], health
    assert transport._runtime is None, "inbound must not be built without an app token"


@pytest.mark.asyncio
async def test_health_says_not_started_before_the_gateway_drives_inbound(store_only_home):
    """Tokens present but ``start_inbound`` never called is its own reportable state.

    Saving config re-cycles the provider and builds a FRESH transport that the gateway does
    not re-drive, so this is not merely a boot-time blink: without it the provider row goes
    green the moment a token is saved and stays green over a receiver that does not exist.
    """
    store = _write_store(
        store_only_home, bot_token="xoxb-fake-store-bot", app_token="xapp-1-fake-store-app"
    )
    health = await SlackDeskTransport(store).health()
    assert health["state"] == "error", health
    assert "NOT STARTED" in health["detail"], health


# ── #953: the allowlist the dashboard writes is the allowlist that is enforced ──


def test_allowed_users_from_the_store_are_authorized(store_only_home):
    """The three people an operator lists must actually be authorized.

    Before #953 this whole test failed twice over: the runtime seeded ``_allowed_users``
    from the owner alone, and ``is_allowed_user`` ignored ``_allowed_users`` regardless.
    """
    _write_store(
        store_only_home,
        allowed_users=[
            {"slack_id": "U_ALICE", "name": "Alice"},
            {"slack_id": "U_BOB", "name": "Bob"},
            {"slack_id": "U_CAROL", "name": "Carol"},
        ],
    )
    runtime = SlackDeskRuntime(_Services("U_OWNER"), config={})
    assert runtime._allowed_users == {"U_ALICE", "U_BOB", "U_CAROL", "U_OWNER"}

    H.set_owner_id("U_OWNER")
    H.set_allowed_users(runtime._allowed_users)
    for uid in ("U_ALICE", "U_BOB", "U_CAROL", "U_OWNER"):
        assert H.is_allowed_user(uid), f"{uid} is on the operator's allowlist and was refused"


def test_an_id_absent_from_the_store_is_refused(store_only_home):
    """Deny-by-default: only ids the operator wrote down are authorized."""
    _write_store(store_only_home, allowed_users=[{"slack_id": "U_ALICE"}])
    runtime = SlackDeskRuntime(_Services("U_OWNER"), config={})
    H.set_owner_id("U_OWNER")
    H.set_allowed_users(runtime._allowed_users)
    assert H.is_allowed_user("U_STRANGER") is False
    assert H.is_allowed_user("") is False


def test_an_empty_allowlist_and_no_owner_authorizes_nobody(store_only_home):
    """Fail-CLOSED. The direction #953 could have failed in, asserted so it cannot drift.

    An unconfigured Slack install must refuse everyone — including the empty sender id an
    unusual event shape can produce. Honouring ``allowed_users`` widened access only for
    ids an operator explicitly listed; with nothing listed there is nothing to widen.
    """
    _write_store(store_only_home)
    runtime = SlackDeskRuntime(_Services(""), config={})
    assert runtime._allowed_users == set()

    H.set_owner_id("")
    H.set_allowed_users(runtime._allowed_users)
    for uid in ("U_ALICE", "U_STRANGER", "U_OWNER", ""):
        assert H.is_allowed_user(uid) is False, f"empty allowlist authorized {uid!r}"


def test_open_channels_still_authorizes_nobody(store_only_home):
    """``open_channels`` is INERT and stays inert in this change — asserted, not assumed.

    ``is_open_channel`` has been a hardcoded ``False`` since this bundle's first public
    commit, so the ``or is_open_channel(channel)`` term in the inbound authorization
    expression can never widen anything. That is the second dashboard control this class
    of defect left dead (see the PR's census), and unlike a per-user allowlist, honouring
    it would authorize *unknown* users by channel membership — a posture change neither
    #952 nor #953 asks for. Pinned here so nobody flips it as "the obvious sibling fix"
    without a deliberate decision, and so the census claim stays true.
    """
    _write_store(store_only_home, open_channels=["C_OPEN"])
    runtime = SlackDeskRuntime(_Services(""), config={})
    assert runtime._open_channels == {"C_OPEN"}, "the field is read; only the gate is inert"

    H.set_open_channels(runtime._open_channels)
    assert H.is_open_channel("C_OPEN") is False
    assert H.is_open_channel("C_OTHER") is False


def test_allowed_enterprise_ids_still_does_not_gate_validation():
    """``allowed_enterprise_ids`` is INERT too, and in the fail-OPEN direction — pinned.

    The third dashboard control this defect class left dead, and the only one whose
    inertness is *permissive*: the field is read (``events.py`` →
    ``settings.enterprise_ids()`` → ``validate_enterprise(extra_ids=…)``) and then ignored,
    which ``validate_enterprise``'s own docstring states outright — "accepted for call-site
    compatibility but no longer gates acceptance". So an operator who lists their Grid org
    id to stop the bot being pointed at a personal workspace (the stated purpose of the
    check) gets no such restriction: any workspace whose bot token authenticates is
    accepted and bound.

    Deliberately NOT changed here. Restoring the gate refuses every install whose workspace
    is not on the list — including every personal/non-Grid workspace, i.e. most of them —
    which is a posture decision, not a bugfix. Asserted so the census claim stays true and
    so the inertness cannot quietly become intentional-looking.
    """
    doc = inspect.getdoc(enterprise.validate_enterprise) or ""
    assert "no longer gates acceptance" in doc, (
        "validate_enterprise's contract changed. If extra_ids gates acceptance again, "
        "allowed_enterprise_ids is live: delete this test and the census entry for it."
    )
    src = inspect.getsource(enterprise.validate_enterprise)
    body = src.split('"""', 2)[-1]
    assert "extra_ids" not in body, (
        "extra_ids is referenced in validate_enterprise's body — it may gate acceptance "
        "again; re-check the census entry for allowed_enterprise_ids."
    )
