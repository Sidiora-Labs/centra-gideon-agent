"""Build the runtime and include available web application bundles."""

import os
import subprocess
import tempfile
from pathlib import Path
from shutil import copy2, copytree

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.dist import Distribution


class RuntimeDistribution(Distribution):
    def has_ext_modules(self):
        return True


class RuntimeBuild(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        self._build_hypermid_daemon(root)
        schemas = root / "spec/hypermid/schemas"
        if schemas.is_dir():
            copytree(
                schemas,
                Path(self.build_lib) / "gideon/hypermid/schemas",
                dirs_exist_ok=True,
            )
        candidates = (root / "apps/console/dist", root / "runtime/gideon/static/dist")
        console_source = root / "apps/console/package.json"
        if (
            not any((source / "index.html").is_file() for source in candidates)
            and console_source.is_file()
        ):
            subprocess.run(
                ["npm", "exec", "--workspace=apps/console", "--", "vite", "build"],
                cwd=root,
                check=True,
            )
        for source in candidates:
            if (source / "index.html").is_file():
                copytree(
                    source,
                    Path(self.build_lib) / "gideon/static/dist",
                    dirs_exist_ok=True,
                )
                break
        assistant = root / "apps/assistant/dist/web"
        if (assistant / "index.html").is_file():
            copytree(
                assistant,
                Path(self.build_lib) / "gideon/static/assistant",
                dirs_exist_ok=True,
            )

    def _build_hypermid_daemon(self, root: Path) -> None:
        executable = "hypermid-daemon.exe" if os.name == "nt" else "hypermid-daemon"
        destination = Path(self.build_lib) / "gideon/hypermid/bin" / executable
        destination.parent.mkdir(parents=True, exist_ok=True)
        prebuilt = os.environ.get("GIDEON_PREBUILT_HYPERMID_DAEMON", "").strip()
        if prebuilt:
            source = Path(prebuilt)
            if not source.is_file():
                raise FileNotFoundError("prebuilt Hypermid daemon does not exist")
            copy2(source, destination)
            if os.name != "nt":
                destination.chmod(0o755)
            return
        if os.environ.get("GIDEON_DEPENDENCIES_ONLY") == "1":
            return
        with tempfile.TemporaryDirectory(prefix="gideon-hypermid-build-") as target:
            environment = os.environ.copy()
            environment["CARGO_TARGET_DIR"] = target
            subprocess.run(
                [
                    "cargo",
                    "build",
                    "--locked",
                    "--release",
                    "-p",
                    "hypermid-daemon",
                    "--manifest-path",
                    os.fspath(root / "Cargo.toml"),
                ],
                cwd=root,
                env=environment,
                check=True,
            )
            copy2(Path(target) / "release" / executable, destination)
        if os.name != "nt":
            destination.chmod(0o755)


setup(cmdclass={"build_py": RuntimeBuild}, distclass=RuntimeDistribution)
