"""Build the runtime and include an available console bundle."""

from pathlib import Path
from shutil import copytree

from setuptools import setup
from setuptools.command.build_py import build_py


class RuntimeBuild(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        candidates = (root / "apps/console/dist", root / "runtime/gideon/static/dist")
        for source in candidates:
            if (source / "index.html").is_file():
                copytree(
                    source,
                    Path(self.build_lib) / "gideon/static/dist",
                    dirs_exist_ok=True,
                )
                break


setup(cmdclass={"build_py": RuntimeBuild})
