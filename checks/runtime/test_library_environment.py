"""Runtime cache and credential isolation for optional native libraries."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_runtime_and_child_library_side_effects_stay_in_home(tmp_path: Path) -> None:
    os_home = tmp_path / "os-home"
    gideon_home = tmp_path / "gideon-home"
    sibling_cache = os_home / ".cache" / "huggingface"
    sibling_cache.mkdir(parents=True)
    (sibling_cache / "token").write_text("hf_sibling_cli_token_not_for_gideon\n")

    script = r'''
import json
import os
import subprocess
import sys
from pathlib import Path

gideon_home = Path(os.environ["GIDEON_HOME"])
assert not gideon_home.exists()

import gideon.__main__
from gideon.core.library_environment import library_environment

settings = library_environment()
assert not gideon_home.exists(), "resolving settings created Gideon's home"
assert os.environ["HF_HOME"] == str(gideon_home / "cache" / "huggingface")
assert os.environ["HF_HUB_CACHE"] == str(gideon_home / "cache" / "huggingface" / "hub")
assert os.environ["HF_XET_CACHE"] == str(gideon_home / "cache" / "huggingface" / "xet")
assert os.environ["TREE_SITTER_LANGUAGE_PACK_CACHE_DIR"] == str(
    gideon_home / "cache" / "tree-sitter-language-pack"
)
assert os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
assert os.environ["DO_NOT_TRACK"] == "1"
assert not (gideon_home / "cache" / "huggingface" / "token").exists()

from gideon.integrations.local_models.huggingface_auth import resolve_token

assert resolve_token().source == "none", "the OS account token was discovered"

# Seed Gideon's real dotenv credential format to verify the explicit approved
# credential source continues to resolve after the library settings are applied.
(gideon_home / ".env").write_text('HF_TOKEN="hf_gideon_approved_token_value"\n')
approved = resolve_token()
assert approved.source == "credential_store"
assert approved.token == "hf_gideon_approved_token_value"

from gideon.security.sandbox import build_child_env

os.environ["HF_TOKEN"] = "hf_parent_token_must_not_enter_children"
child = build_child_env(site="library-environment-check")
assert "HF_TOKEN" not in child
assert "HUGGING_FACE_HUB_TOKEN" not in child
assert "HF_TOKEN_PATH" not in child
assert child["HF_HOME"] == str(gideon_home / "cache" / "huggingface")
assert child["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
assert child["TREE_SITTER_LANGUAGE_PACK_CACHE_DIR"] == str(
    gideon_home / "cache" / "tree-sitter-language-pack"
)

child_probe = subprocess.run(
    [sys.executable, "-c", "import json, os; print(json.dumps({k: os.environ[k] for k in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_HUB_DISABLE_IMPLICIT_TOKEN', 'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK', 'TREE_SITTER_LANGUAGE_PACK_CACHE_DIR')}))"],
    env=child,
    check=True,
    capture_output=True,
    text=True,
)
reported = json.loads(child_probe.stdout)
assert reported["HF_HOME"] == child["HF_HOME"]
assert reported["HF_XET_CACHE"] == child["HF_XET_CACHE"]

from gideon.assurance.codegraph.parse import parse_source

parsed = parse_source("sample.py", b"def answer():\n    return 42\n")
assert any(item.name == "answer" for item in parsed.definitions), parsed

from tree_sitter_language_pack import cache_dir

parser_cache = Path(cache_dir()).resolve()
assert parser_cache.is_relative_to(gideon_home.resolve()), parser_cache
'''

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(os_home),
            "GIDEON_HOME": str(gideon_home),
            "GIDEON_CREDENTIAL_BACKEND": "dotenv",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(__file__).parents[2] / "runtime"),
            "HF_HOME": str(tmp_path / "wrong-hf-home"),
            "HF_HUB_CACHE": str(tmp_path / "wrong-hub-cache"),
            "HF_XET_CACHE": str(tmp_path / "wrong-xet-cache"),
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "0",
            "HF_HUB_DISABLE_TELEMETRY": "0",
            "DO_NOT_TRACK": "0",
            "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR": str(tmp_path / "wrong-parser-cache"),
        }
    )
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_TOKEN_PATH"):
        env.pop(name, None)

    subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert (sibling_cache / "token").read_text() == "hf_sibling_cli_token_not_for_gideon\n"
    assert not (os_home / ".cache" / "tree-sitter-language-pack").exists()
