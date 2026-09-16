"""Gideon skills package — marketplace abstraction, native + skills.sh clients, and ProcedureLibrary."""  # noqa: E501

# Re-export the pre-existing ProcedureLibrary and helpers from the loader module
# so existing callers (`from gideon.extensions.skills import ProcedureLibrary`) continue to work.
from gideon.extensions.skills import native as _native  # noqa: F401
from gideon.extensions.skills.loader import (
    AUTO_SKILL_MAX_PROCEDURE_CHARS,
    AutoSkillProvenance,
    ProcedureLibrary,
    _auto_name_from_title,
)
from gideon.extensions.skills.marketplace import (
    SkillEntry,
    SkillsMarketplace,
    SkillsRegistry,
    get_default_skills_registry,
)

__all__ = [
    "AUTO_SKILL_MAX_PROCEDURE_CHARS",
    "AutoSkillProvenance",
    "ProcedureLibrary",
    "SkillEntry",
    "SkillsMarketplace",
    "SkillsRegistry",
    "get_default_skills_registry",
    "_auto_name_from_title",
]
