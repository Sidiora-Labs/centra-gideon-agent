"""Build the runtime and include available web application bundles."""

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
        assistant = root / "apps/assistant/dist/web"
        if (assistant / "index.html").is_file():
            copytree(
                assistant,
                Path(self.build_lib) / "gideon/static/assistant",
                dirs_exist_ok=True,
            )


setup(cmdclass={"build_py": RuntimeBuild})
