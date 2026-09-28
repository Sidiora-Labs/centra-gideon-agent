from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_dotenv_credentials_roundtrip_exact_values_without_extra_entries(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    values = {
        "GIDEON_SECRET_MCP_SAMPLE_SERVICE_TOKEN": "invalid-sample-first\nsecond-line",
        "GIDEON_SECRET_MCP_SAMPLE_AUTHORIZATION": "Bearer invalid-sample-header",
        "GIDEON_SECRET_MCP_SAMPLE_SPECIAL": '  invalid-sample-"quoted"\\path\r\nEXTRA_CREDENTIAL=invalid-sample\t\u2028\u00e9  ',
    }
    for key in values:
        monkeypatch.delenv(key, raising=False)
    env_path = home / ".env"
    env_path.write_text("# sample configuration\nLEGACY_SAMPLE=legacy-value\n", encoding="utf-8")

    from gideon.core.config import credentials

    for key, value in values.items():
        credentials.put_secret_value(key, value)
        assert credentials.get_secret_value(key) == value
        assert key not in os.environ
    assert credentials.get_secret_value("LEGACY_SAMPLE") == "legacy-value"
    assert set(credentials.DotenvDocument(env_path).names()) == {"LEGACY_SAMPLE", *values}
    assert env_path.stat().st_mode & 0o777 == 0o600
    assert len(env_path.read_text(encoding="utf-8").splitlines()) == len(values) + 2

    restarted = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, os, sys; from gideon.core.config import credentials; "
            "expected = json.load(sys.stdin); "
            "assert all(credentials.get_secret_value(key) == value for key, value in expected.items()); "
            "assert all(key not in os.environ for key in expected); "
            "assert credentials.get_secret_value('LEGACY_SAMPLE') == 'legacy-value'",
        ],
        input=json.dumps(values),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(credentials.__file__).resolve().parents[3])},
        check=False,
    )
    assert restarted.returncode == 0, restarted.stderr
    replaced_key = next(iter(values))
    credentials.put_secret_value(replaced_key, "invalid-sample-replacement")
    assert credentials.get_secret_value(replaced_key) == "invalid-sample-replacement"
    assert credentials.delete_secret_value(replaced_key)
    assert credentials.get_secret_value(replaced_key) == ""
    for key, value in values.items():
        if key != replaced_key:
            assert credentials.get_secret_value(key) == value
