import base64
import hashlib
import json
import multiprocessing
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from checks.runtime.capabilities.music.test_listening import store_at
from gideon.sdk.credentials import CredentialStore
from gideon.workspace.capabilities.music.spotify import SpotifyBridge
from gideon.workspace.capabilities.music.spotify_oauth import SCOPES, SpotifyOAuth
from gideon.workspace.capabilities.music.store import DomainError


def write_credentials(home, index):
    store = CredentialStore(home)
    for number in range(8):
        store.put(
            f"worker-{index}-{number}",
            {"type": "static_token", "value": f"value-{index}-{number}"},
        )
    store.remove(f"worker-{index}-0")


def test_atomic_distinct_process_updates_preserve_unrelated(tmp_path):
    store = CredentialStore(tmp_path)
    store.save({"existing": {"type": "api_key", "value": "retained"}})
    context = multiprocessing.get_context("fork")
    processes = [
        context.Process(target=write_credentials, args=(tmp_path, index))
        for index in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    store.reload()
    assert store.resolve("existing").secret == "retained"
    assert len(store.list()) == 29
    for index in range(4):
        assert not store.has(f"worker-{index}-0")
        for number in range(1, 8):
            assert (
                store.resolve(f"worker-{index}-{number}").secret
                == f"value-{index}-{number}"
            )
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / ".credentials.lock").stat().st_mode & 0o777 == 0o600
    assert all(row.secret is None for row in store.list())


def test_put_remove_validation_and_reopen(tmp_path):
    store = CredentialStore(tmp_path)
    store.put("one", {"type": "oauth2", "value": "first"})
    second = CredentialStore(tmp_path)
    second.put("two", {"type": "api_key", "value": "second"})
    store.put("one", {"type": "oauth2", "value": "updated"})
    second.reload()
    assert second.resolve("one").secret == "updated"
    assert second.resolve("two").secret == "second"
    store.remove("missing")
    store.remove("one")
    final = CredentialStore(tmp_path)
    assert [row.name for row in final.list()] == ["two"]
    with pytest.raises(ValueError):
        store.put("", {})
    with pytest.raises(ValueError):
        store.put("name", None)
    with pytest.raises(ValueError):
        store.remove("")
    assert final.resolve("two").secret == "second"


def oauth_at(home):
    bridge = SpotifyBridge(home, store_at(home))
    bridge.configure(
        {
            "enabled": True,
            "credential_name": "spotify-own",
            "account_label": "Own",
            "revision": 0,
        }
    )
    return SpotifyOAuth(bridge)


def test_actual_pkce_challenge_private_verifier_and_reopen(tmp_path):
    oauth = oauth_at(tmp_path)
    result = oauth.begin(
        {
            "client_id": "registered-client",
            "redirect_uri": "http://127.0.0.1:8765/callback",
        }
    )
    parsed = urlsplit(result["authorization_url"])
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.netloc == "accounts.spotify.com"
    assert parsed.path == "/authorize"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["registered-client"]
    assert query["scope"] == [SCOPES]
    assert query["state"] == [result["state"]]
    assert query["code_challenge_method"] == ["S256"]
    assert result["expires_in"] == 600
    pending = oauth.pending(result["state"])
    assert 43 <= len(pending["verifier"]) <= 128
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert query["code_challenge"] == [expected]
    assert pending["verifier"] not in json.dumps(result)
    reopened = SpotifyOAuth(SpotifyBridge(tmp_path, store_at(tmp_path)))
    assert reopened.pending(result["state"]) == pending
    assert pending["credential_name"] == "spotify-own"
    assert pending["expires_at"] > time.time()
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "redirect",
    [
        "http://example.com/callback",
        "http://localhost/callback",
        "https://user@example.com/callback",
        "https://example.com/#fragment",
        "file:///tmp/callback",
        "https:///missing",
    ],
)
def test_pkce_rejects_unsafe_redirects(tmp_path, redirect):
    oauth = oauth_at(tmp_path)
    with pytest.raises(DomainError):
        oauth.begin({"client_id": "client", "redirect_uri": redirect})
    assert oauth.credentials.list() == []


def test_expired_and_unknown_state_cannot_exchange(tmp_path):
    oauth = oauth_at(tmp_path)
    result = oauth.begin(
        {"client_id": "client", "redirect_uri": "https://example.com/callback"}
    )
    pending = oauth.pending(result["state"])
    pending["expires_at"] = 0
    oauth.credentials.put(
        "spotify-pkce-" + result["state"],
        {"type": "oauth2", "value": json.dumps(pending)},
    )
    with pytest.raises(DomainError) as error:
        oauth.pending(result["state"])
    assert error.value.code == "oauth_state"
    assert not oauth.credentials.has("spotify-pkce-" + result["state"])
    with pytest.raises(DomainError):
        oauth.pending("unknown")


@pytest.mark.asyncio
async def test_saved_token_bundle_refresh_metadata_and_raw_named_token(tmp_path):
    oauth = oauth_at(tmp_path)
    oauth.credentials.put("unrelated", {"type": "api_key", "value": "other"})
    oauth._save(
        "spotify-own",
        "client",
        {
            "access_token": "issued-access",
            "refresh_token": "issued-refresh",
            "expires_in": 3600,
        },
    )
    assert await oauth.access("spotify-own") == "issued-access"
    assert oauth.bridge._credential("spotify-own") == "issued-access"
    assert oauth.bridge.readiness()["ready_to_sync"]
    assert oauth.credentials.resolve("unrelated").secret == "other"
    oauth._save(
        "spotify-own",
        "client",
        {"access_token": "rotated-access", "expires_in": 3600},
        "issued-refresh",
    )
    bundle = json.loads(oauth.credentials.resolve("spotify-own").secret)
    assert bundle["refresh_token"] == "issued-refresh"
    assert bundle["client_id"] == "client"
    assert await oauth.access("spotify-own") == "rotated-access"
    oauth.credentials.put(
        "direct", {"type": "oauth2", "value": "external-managed-access"}
    )
    assert await oauth.access("direct") == "external-managed-access"
    assert await oauth.access("absent") is None
    oauth._save("expired", "client", {"access_token": "expired", "expires_in": 1})
    with pytest.raises(DomainError) as error:
        await oauth.access("expired")
    assert error.value.code == "oauth_expired"
    with pytest.raises(DomainError):
        await oauth.complete({"state": "unknown", "code": "authorization-code"})
