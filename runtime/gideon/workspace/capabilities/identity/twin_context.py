from pathlib import Path

from gideon.core.config.loader import config_dir
from gideon.workspace.capabilities.identity.twin import TwinStore


def standing_human_context(session_key, *, home=None, budget=1000):
    if not isinstance(session_key, str) or not session_key.startswith(('dashboard:', 'cli:')):
        return ''
    path = Path(home) if home is not None else config_dir()
    path = path / 'capabilities' / 'identity' / 'twin.sqlite3'
    if not path.is_file():
        return ''
    result = TwinStore(path).compose(budget=budget, include_private=False)
    return result['text']
