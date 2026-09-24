import os
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("installed", [True, False])
def test_doctor_stt_dependency_never_loads_torch(installed, tmp_path):
    script = textwrap.dedent("""
        import importlib.util
        import sys

        def refuse_heavy_import(event, args):
            if event == "import" and args[0].split(".")[0] in {"torch", "faster_whisper"}:
                raise AssertionError(f"doctor imported heavyweight runtime: {args[0]}")

        sys.addaudithook(refuse_heavy_import)
        from gideon.interfaces.cli.doctor import _doctor_stt_dependency

        installed = sys.argv[1] == "True"
        if installed:
            assert importlib.util.find_spec("faster_whisper") is not None, "real faster-whisper installation required"
        else:
            sys.path[:] = []
            assert importlib.util.find_spec("faster_whisper") is None

        issues = _doctor_stt_dependency()
        assert issues == ([] if installed else ["faster_whisper missing"])
        assert not any(name == "torch" or name.startswith("torch.") for name in sys.modules)
        assert "faster_whisper" not in sys.modules
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(installed)],
        env={**os.environ, "GIDEON_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "installed (runtime not loaded)" if installed else "missing"
    ) in result.stdout
