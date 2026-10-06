from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "tooling/scripts/verify_wheel.py"
SPEC = importlib.util.spec_from_file_location("verify_wheel", VERIFIER)
assert SPEC is not None and SPEC.loader is not None
WHEEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WHEEL)


def test_wheel_notice_reader_returns_the_packaged_customer_bytes(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "gideon-0.1.0-py3-none-any.whl"
    notice = b"Gideon third-party notices\nMIT License\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("gideon/static/dist/index.html", "<!doctype html>")
        archive.writestr("gideon/static/dist/THIRD_PARTY_NOTICES.txt", notice)

    assert WHEEL._notice_bytes_in_wheel(wheel) == notice
