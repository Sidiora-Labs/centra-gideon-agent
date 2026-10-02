from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from checks.hypermid.packaging.source_closure import freeze_source_tree


ROOT = Path(__file__).resolve().parents[2]
HELPER = Path(__file__).resolve().parent / "packaging" / "clean_install.py"


def test_clean_wheel_and_single_container_install_real_hypermid() -> None:
    log = Path(
        os.environ.get(
            "HYPERMID_PACKAGED_INSTALL_LOG",
            os.fspath(Path(tempfile.gettempdir()) / "hypermid-packaged-install.log"),
        )
    )
    console = subprocess.run(
        ["npm", "exec", "--workspace=apps/console", "--", "vite", "build"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert console.returncode == 0, (
        "published console build failed before source freeze\n"
        + console.stdout
        + console.stderr
    )
    assert (ROOT / "apps/console/dist/index.html").is_file()
    with tempfile.TemporaryDirectory(prefix="hypermid-packaged-source-") as temporary:
        root = Path(temporary)
        frozen = root / "source"
        artifacts = root / "artifacts"
        closure = freeze_source_tree(ROOT, frozen)
        result = subprocess.run(
            [
                sys.executable,
                os.fspath(HELPER),
                "--log",
                os.fspath(log),
                "--source-root",
                os.fspath(frozen),
                "--artifact-dir",
                os.fspath(artifacts),
                "--source-digest",
                str(closure["source_digest"]),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=3_900,
        )
        assert result.returncode == 0, (
            f"clean install failed with exit {result.returncode}; "
            f"log={log}\n{result.stderr}"
        )
        source_manifest = json.loads(
            (artifacts / "source-closure.json").read_text(encoding="utf-8")
        )
        wheel_manifest = json.loads(
            (artifacts / "wheel-manifest.json").read_text(encoding="utf-8")
        )
        image_manifest = json.loads(
            (artifacts / "image-manifest.json").read_text(encoding="utf-8")
        )
        assert source_manifest == closure
        assert wheel_manifest["source_digest"] == closure["source_digest"]
        assert image_manifest["source_digest"] == closure["source_digest"]
        wheels = tuple(artifacts.glob("*.whl"))
        assert len(wheels) == 1
        assert wheel_manifest["artifact"] == wheels[0].name
        evidence = json.loads(result.stdout)
    assert evidence["container_user"] in {"gideon", "10001", "10001:10001"}
    assert evidence["wheel"]["entry_count"] > 0
    evidence["container_user"] = "unprivileged"
    evidence["wheel"]["entry_count"] = "present"
    assert evidence == {
        "cli_status": "passed",
        "configured_root_persistence": "passed",
        "console": "present",
        "container_user": "unprivileged",
        "daemon_health": "passed",
        "default_mode": "off",
        "python_adapter": "passed",
        "transport": "authenticated_unix",
        "wheel": {
            "daemon_path": "gideon/hypermid/bin/hypermid-daemon",
            "entry_count": "present",
            "platform_wheel": True,
        },
    }
