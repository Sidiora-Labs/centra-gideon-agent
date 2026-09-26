"""Put the app dir on sys.path so app tests import the ``discord_desk`` package the
way the gateway's app loader does at runtime, and pin an isolated home.

Every core surface this bundle touches — ``ProviderSettings`` (the app store),
``channel_trust`` (the trust store), ``AppConfig`` (the credential store) — routes
its path through ``config_dir()``, which re-reads ``GIDEON_HOME`` live on each call.
Pointing that at a per-test tmp dir isolates all of them at once, so nothing touches
the real ``~/.gideon`` (the lesson: patching ``config_dir`` alone misses
import-bound stores — set the env)."""


import sys
from pathlib import Path

import pytest

_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))


#: Credential keys these tests write. Core's ``save_credential`` MIRRORS every value
#: into ``os.environ`` (so a running gateway sees it immediately) and
#: ``load_credentials`` reads env vars back for known keys — which means a tmp
#: GIDEON_HOME is NOT sufficient isolation on its own: a credential written by
#: one test stays visible to the next through the process environment, and a
#: "missing credential" assertion silently passes on the previous test's value.
_CREDENTIAL_KEYS = ("DISCORD_BOT_TOKEN", "GIDEON_OWNER_ID")


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory, monkeypatch):
    """Point GIDEON_HOME at a per-test tmp dir AND clear the credential env.

    The home covers the file-backed stores (app store, trust store, ``.env``); the
    env clearing covers the process-global mirror described above. monkeypatch undoes
    both after each test, so neither the real home nor a sibling test is affected."""
    home = tmp_path_factory.mktemp("gid-desk-home")
    monkeypatch.setenv("GIDEON_HOME", str(home))
    for key in _CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    return home


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Drop the module-global settings cache around every test (it's a deliberate
    process singleton, refreshed on write — reset it so one test's activation mode
    can't leak into the next)."""
    from discord_desk import settings as s

    s._settings = None
    yield
    s._settings = None


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
