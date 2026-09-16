"""Check packaged Python syntax, runtime resources and the isolated CLI entry point."""

import argparse
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    options = parser.parse_args()
    with zipfile.ZipFile(options.wheel) as archive:
        files = archive.namelist()
        modules = [name for name in files if name.endswith(".py")]
        assert modules and all(name.startswith("gideon/") for name in modules)
        for name in modules:
            compile(archive.read(name), name, "exec")
        for required in (
            "gideon/core/config/defaults.json",
            "gideon/static/dist/index.html",
            "gideon/engine/lifecycle.py",
            "gideon/engine/services.py",
            "gideon/sdk/tool.py",
        ):
            assert required in files, required
        with tempfile.TemporaryDirectory(prefix="gideon-package-check-") as temporary:
            directory = Path(temporary)
            archive.extractall(directory)
            driver = """
import os, pathlib, runpy, sys
root = pathlib.Path(sys.argv[1])
os.environ['GIDEON_HOME'] = str(root / 'isolated-home')
sys.path.insert(0, str(root))
import gideon
assert pathlib.Path(gideon.__file__).is_relative_to(root)
from gideon.core.layout import console_dist
assert console_dist().is_relative_to(root)
sys.argv = ['gideon', '--help']
runpy.run_module('gideon', run_name='__main__')
"""
            result = subprocess.run(
                [sys.executable, "-I", "-c", driver, str(directory)],
                text=True,
                capture_output=True,
                timeout=30,
                cwd=directory,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert "gateway" in result.stdout and "snapshot" in result.stdout
    print(
        f"Verified {len(modules)} Python modules, bundled console resources, and isolated CLI startup"
    )


if __name__ == "__main__":
    main()
