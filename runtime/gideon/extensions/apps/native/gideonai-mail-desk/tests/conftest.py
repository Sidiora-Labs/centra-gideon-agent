"""Put the app dir on sys.path so app tests import the ``mail_desk_runtime`` package the
way the gateway's app loader does at runtime, and pin an isolated home.

Every core surface this app touches — ``ProviderSettings`` (the app store),
``channel_trust`` (the trust store), ``AppConfig`` (the credential store),
``app_data_dir`` (the UID cursor + thread state) — routes its path through
``config_dir()``, which re-reads ``GIDEON_HOME`` live on each call. Pointing that at a
per-test tmp dir isolates all of them at once, so nothing touches the real ``~/.gideon``
(the lesson: patching ``config_dir`` alone misses import-bound stores — set the env).
"""

import sys
from pathlib import Path

import pytest

_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))


#: Credential keys these tests write. Core's ``save_credential`` MIRRORS every value into
#: ``os.environ`` (so a running gateway sees it immediately), which means a tmp GIDEON_HOME
#: is NOT sufficient isolation on its own: a credential written by one test stays visible
#: to the next through the process environment, and a "missing credential" assertion
#: silently passes on the previous test's value.
_CREDENTIAL_KEYS = ("MAIL_DESK_IMAP_PASS", "MAIL_DESK_SMTP_PASS", "GIDEON_OWNER_ID")


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory, monkeypatch):
    """Point GIDEON_HOME at a per-test tmp dir AND clear the credential env."""
    home = tmp_path_factory.mktemp("gid-mail-home")
    monkeypatch.setenv("GIDEON_HOME", str(home))
    for key in _CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    return home


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Drop the module-global settings cache around every test (a deliberate process
    singleton, refreshed on write) so one test's config can't leak into the next."""
    from mail_desk_runtime import settings as s

    s._settings = None
    yield
    s._settings = None


@pytest.fixture(autouse=True)
def _reset_sel_singleton():
    """The SEL is a process singleton that pins its home on first init. Reset it around
    each test so it re-binds under this test's GIDEON_HOME (else the first test's
    home is reused for every later assertion).

    The SEL *class* isn't an SDK export, so reach it SDK-legally via ``type(sel())`` —
    ``sel`` is re-exported from ``gideon.sdk.channel``. Getting the class this way
    may spin up the current singleton, which we immediately discard."""
    from gideon.sdk.channel import sel

    cls = type(sel())
    cls._instance = None
    cls._initialized = False
    yield
    cls._instance = None
    cls._initialized = False


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
