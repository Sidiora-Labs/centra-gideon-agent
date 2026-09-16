"""Exercise installed entrypoints and resources after the domain relocation."""

import os
import subprocess
import sys

from gideon.core.layout import console_dist, package_path


def test_command_line_starts_from_an_isolated_home(tmp_path):
    environment = {**os.environ, "GIDEON_HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "gideon", "--help"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "gateway" in result.stdout
    assert "snapshot" in result.stdout


def test_provider_contract_is_packaged_under_gideon():
    import importlib.metadata

    from gideon.sdk.tool import ToolProvider

    entry = next(
        item
        for item in importlib.metadata.entry_points(group="console_scripts")
        if item.name == "gideon"
    )
    assert entry.value == "gideon.interfaces.cli.main:main"
    assert ToolProvider.__module__ == "gideon.integrations.tool_providers.base"


def test_security_and_console_resources_remain_available():
    from gideon.security.security import BUILTIN_DENY_PATTERNS
    from gideon.security.supply_chain import load_scan_rule_gloss

    assert BUILTIN_DENY_PATTERNS
    assert load_scan_rule_gloss()
    assert package_path("core", "config", "defaults.json").is_file()
    bundle = console_dist()
    assert bundle is not None
    assert (bundle / "index.html").is_file()
