"""Put the app dir on sys.path so app tests import the ``slack_desk_runtime`` package
the way the gateway's app loader does at runtime.

Also hosts the slack-suite autouse fixtures that used to live in the CORE test
conftest (moved here with the slack-internal tests so the core suite runs on a
standalone clone with no sibling apps/ directory)."""

import asyncio
import sys
from pathlib import Path

import pytest

_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from slack_desk_runtime.handler import _PHASE_EMOJIS, _build_phase_emojis  # noqa: E402


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Ensure a current event loop exists for code that constructs asyncio
    primitives (e.g. Semaphore) at import/init time outside a running loop."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def _isolate_session_map(tmp_path_factory, monkeypatch):
    """Point the SESSION MAP at a per-test tmp dir so nothing touches the real
    ~/.gideon/session_map.json (SessionManager construction rewrites it)."""
    map_home = tmp_path_factory.mktemp("gid-sessmap")
    monkeypatch.setattr("gideon.engine.session_map.config_dir", lambda: map_home)


@pytest.fixture(autouse=True)
def _isolate_migration_marker(tmp_path_factory, monkeypatch):
    """Point migrate_from_core's done-marker FILE at a per-test tmp path,
    pre-created so any unpatched SlackDeskSettings.load() short-circuits the
    migration (never reads the real core config.json or touches the real
    app data dir). Migration tests re-patch _migration_marker_path themselves."""
    marker = tmp_path_factory.mktemp("gid-migmark") / ".core_migration_done"
    marker.touch()
    monkeypatch.setattr("slack_desk_runtime.settings._migration_marker_path", lambda: marker)


@pytest.fixture(autouse=True)
def _isolate_channel_trust(tmp_path_factory, monkeypatch):
    """Point core's channel_trust entity store (and GIDEON_HOME, which its
    SEL audit rows resolve through) at a per-test tmp dir. The EA-7 write-throughs
    fire on owner actions (Allow/Deny/Track buttons, owner claim, thread linking),
    so without this every such test would write the REAL ~/.gideon."""
    home = tmp_path_factory.mktemp("gid-trust")
    monkeypatch.setattr(
        "gideon.extensions.providers.entity_routes._entity_settings_path",
        lambda entity: home / "entity_settings" / f"{entity}.json",
    )
    monkeypatch.setenv("GIDEON_HOME", str(home))
    yield


@pytest.fixture(autouse=True)
def _reset_trust_mode():
    """Reset the process-global YOLO/auto-approve trust state around every test
    (``gideon.trust_mode`` is a deliberate process singleton)."""
    import importlib

    _tm = importlib.import_module("gideon.security.trust_mode")
    _tm._TRUST.disable()
    yield
    _tm._TRUST.disable()


@pytest.fixture(autouse=True)
def _enterprise_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a default validated team_id so _route_message doesn't reject messages."""
    monkeypatch.setattr("slack_desk_runtime.enterprise._validated_team_id", "TTEST")
    monkeypatch.setattr("slack_desk_runtime.enterprise._validated_enterprise_id", "ETEST")


@pytest.fixture(autouse=True)
def _clean_emojis():
    """Reset _PHASE_EMOJIS to defaults before each test (suppresses local config)."""
    original = dict(_PHASE_EMOJIS)
    _PHASE_EMOJIS.clear()
    _PHASE_EMOJIS.update(_build_phase_emojis({})[0])
    yield
    _PHASE_EMOJIS.clear()
    _PHASE_EMOJIS.update(original)


@pytest.fixture(autouse=True)
def _reset_slack_desk_allowlist():
    """Reset the Slack handler's module-global allowlist/owner/channel state around
    every test. These are process-globals (owner-claim, tracked channels, open
    channels) that otherwise leak across test files and skew message-routing tests."""
    import slack_desk_runtime.handler as h

    saved = (h._owner_id, set(h._allowed_users), set(h._tracking_channels), set(h._open_channels))
    h._owner_id = ""
    h._allowed_users = set()
    h._tracking_channels = set()
    h._open_channels = set()
    yield
    h._owner_id, _au, _tc, _oc = saved
    h._allowed_users = _au
    h._tracking_channels = _tc
    h._open_channels = _oc


@pytest.fixture(autouse=True)
def _live_writes_baseline(monkeypatch):
    """Pin the live-writes kill switch OFF as the suite-wide baseline.

    ``transport.send()`` returns a typed :class:`SendRefused` (falsy, not a bool) while
    ``GIDEON_DISABLE_LIVE_WRITES`` is set, and core's channel conformance kit
    asserts ``send()`` returns a ``bool``. Both are correct in their own frame, so the
    guard state has to be an EXPLICIT precondition rather than whatever the ambient
    environment happens to carry: a developer (or a CI job) exporting the var would
    otherwise turn the conformance clause red for a reason unrelated to the change
    being tested. Tests that want the guard ON set it themselves with monkeypatch."""
    monkeypatch.delenv("GIDEON_DISABLE_LIVE_WRITES", raising=False)
