"""Per-agent routing notes stored as UTF-8 Markdown documents."""

import re
from pathlib import Path

from gideon.core.config import loader as config_loader

METADATA_DIR_NAME = "agent-metadata"
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_name(name: str) -> str:
    if name and _SAFE_NAME_RE.fullmatch(name):
        return name
    raise ValueError(f"Invalid agent name: {name!r}")


def config_dir() -> Path:
    return config_loader.config_dir()


def metadata_dir() -> Path:
    directory = config_dir().joinpath(METADATA_DIR_NAME)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class _MarkdownShelf:
    def __init__(self):
        self.root = metadata_dir()

    def document(self, name):
        return self.root.joinpath(_validate_name(name) + ".md")

    def read(self, path):
        try:
            with path.open(encoding="utf-8") as stream:
                return stream.read()
        except FileNotFoundError:
            return ""

    def write(self, path, content):
        with path.open("w", encoding="utf-8") as stream:
            stream.write(content)
        return path

    def remove(self, path):
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def snapshot(self):
        result = {}
        for path in sorted(self.root.glob("*.md")):
            with path.open(encoding="utf-8") as stream:
                result[path.stem] = stream.read()
        return result


def load(name: str) -> str:
    shelf = _MarkdownShelf()
    return shelf.read(shelf.document(name))


def save(name: str, content: str) -> Path:
    shelf = _MarkdownShelf()
    return shelf.write(shelf.document(name), content)


def delete(name: str) -> bool:
    shelf = _MarkdownShelf()
    return shelf.remove(shelf.document(name))


def load_all() -> dict[str, str]:
    return _MarkdownShelf().snapshot()
