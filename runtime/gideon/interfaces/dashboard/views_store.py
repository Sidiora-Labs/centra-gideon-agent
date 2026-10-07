"""Compatibility module for the canonical home-scoped composition store."""

import sys

from gideon.workspace.capabilities.platform import compositions
from gideon.workspace.capabilities.platform.compositions import _ADDED_BY as _ADDED_BY
from gideon.workspace.capabilities.platform.compositions import (
    _MISSION_CONTROL_CORE_REFS as _MISSION_CONTROL_CORE_REFS,
)
from gideon.workspace.capabilities.platform.compositions import (
    _OVERVIEW_CORE_REFS as _OVERVIEW_CORE_REFS,
)
from gideon.workspace.capabilities.platform.compositions import (
    _REFRESH_MODES as _REFRESH_MODES,
)
from gideon.workspace.capabilities.platform.compositions import _SIZES as _SIZES
from gideon.workspace.capabilities.platform.compositions import (
    _STORE_FILENAME as _STORE_FILENAME,
)
from gideon.workspace.capabilities.platform.compositions import (
    CORE_COMPOSITION_REFS as CORE_COMPOSITION_REFS,
)
from gideon.workspace.capabilities.platform.compositions import (
    PRESET_MISSION_CONTROL_ID as PRESET_MISSION_CONTROL_ID,
)
from gideon.workspace.capabilities.platform.compositions import (
    PRESET_OVERVIEW_ID as PRESET_OVERVIEW_ID,
)
from gideon.workspace.capabilities.platform.compositions import (
    DashboardTile as DashboardTile,
)
from gideon.workspace.capabilities.platform.compositions import (
    DashboardView as DashboardView,
)
from gideon.workspace.capabilities.platform.compositions import (
    PresetLockedError as PresetLockedError,
)
from gideon.workspace.capabilities.platform.compositions import (
    TileDataNode as TileDataNode,
)
from gideon.workspace.capabilities.platform.compositions import (
    TileRefresh as TileRefresh,
)
from gideon.workspace.capabilities.platform.compositions import (
    ViewNotFoundError as ViewNotFoundError,
)
from gideon.workspace.capabilities.platform.compositions import (
    _empty_disk as _empty_disk,
)
from gideon.workspace.capabilities.platform.compositions import _is_preset as _is_preset
from gideon.workspace.capabilities.platform.compositions import _max_tiles as _max_tiles
from gideon.workspace.capabilities.platform.compositions import (
    _mission_control_preset as _mission_control_preset,
)
from gideon.workspace.capabilities.platform.compositions import (
    _mutate_disk as _mutate_disk,
)
from gideon.workspace.capabilities.platform.compositions import (
    _overlay_tiles as _overlay_tiles,
)
from gideon.workspace.capabilities.platform.compositions import (
    _overview_preset as _overview_preset,
)
from gideon.workspace.capabilities.platform.compositions import _presets as _presets
from gideon.workspace.capabilities.platform.compositions import _read_disk as _read_disk
from gideon.workspace.capabilities.platform.compositions import (
    _refresh_from_dict as _refresh_from_dict,
)
from gideon.workspace.capabilities.platform.compositions import (
    _tile_from_dict as _tile_from_dict,
)
from gideon.workspace.capabilities.platform.compositions import (
    _user_view_from_dict as _user_view_from_dict,
)
from gideon.workspace.capabilities.platform.compositions import (
    _views_from_data as _views_from_data,
)
from gideon.workspace.capabilities.platform.compositions import add_tile as add_tile
from gideon.workspace.capabilities.platform.compositions import (
    composition_state as composition_state,
)
from gideon.workspace.capabilities.platform.compositions import config_dir as config_dir
from gideon.workspace.capabilities.platform.compositions import (
    create_view as create_view,
)
from gideon.workspace.capabilities.platform.compositions import (
    delete_view as delete_view,
)
from gideon.workspace.capabilities.platform.compositions import find_tile as find_tile
from gideon.workspace.capabilities.platform.compositions import get_view as get_view
from gideon.workspace.capabilities.platform.compositions import list_views as list_views
from gideon.workspace.capabilities.platform.compositions import load_views as load_views
from gideon.workspace.capabilities.platform.compositions import (
    resolve_tile as resolve_tile,
)
from gideon.workspace.capabilities.platform.compositions import (
    set_composition as set_composition,
)
from gideon.workspace.capabilities.platform.compositions import (
    set_tile_refresh as set_tile_refresh,
)
from gideon.workspace.capabilities.platform.compositions import (
    update_view as update_view,
)
from gideon.workspace.capabilities.platform.compositions import views_path as views_path

__all__ = [
    "config_dir",
    "_STORE_FILENAME",
    "_SIZES",
    "_ADDED_BY",
    "_REFRESH_MODES",
    "PRESET_OVERVIEW_ID",
    "_OVERVIEW_CORE_REFS",
    "PRESET_MISSION_CONTROL_ID",
    "_MISSION_CONTROL_CORE_REFS",
    "PresetLockedError",
    "ViewNotFoundError",
    "TileDataNode",
    "TileRefresh",
    "DashboardTile",
    "DashboardView",
    "views_path",
    "_empty_disk",
    "_read_disk",
    "_mutate_disk",
    "_refresh_from_dict",
    "_tile_from_dict",
    "_overlay_tiles",
    "_overview_preset",
    "_mission_control_preset",
    "_presets",
    "_user_view_from_dict",
    "_views_from_data",
    "load_views",
    "list_views",
    "get_view",
    "_is_preset",
    "create_view",
    "update_view",
    "delete_view",
    "_max_tiles",
    "add_tile",
    "set_tile_refresh",
    "find_tile",
    "resolve_tile",
    "CORE_COMPOSITION_REFS",
    "composition_state",
    "set_composition",
]

# Preserve module identity, including existing config_dir monkeypatch consumers.
sys.modules[__name__] = compositions
