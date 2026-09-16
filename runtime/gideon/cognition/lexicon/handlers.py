"""HTTP handlers for /api/lexicon/* — the user-facing Vocabulary surface (core LEX.6).

List terms (source-badged: graph / manual / learned), add a manual term (+aliases), prune
(disable) / delete, rebuild from the knowledge graph, and view + toggle learned corrections'
auto_apply. The Minutes app's transcript-edit UX also POSTs corrections here (LEX.5), gated
by its ``/api/lexicon`` permission.
"""

from __future__ import annotations

import logging
import sys

from aiohttp import web

from gideon.cognition.lexicon import get_lexicon_service
from gideon.cognition.lexicon.vocabulary_http import VocabularyHttp

logger = logging.getLogger(__name__)


def _term_dict(t) -> dict:
    return VocabularyHttp.project(t, VocabularyHttp.term_fields)


def _corr_dict(c) -> dict:
    return VocabularyHttp.project(c, VocabularyHttp.correction_fields)


async def api_lexicon_terms(request: web.Request) -> web.Response:
    """GET /api/lexicon/terms?source=&search= — list vocabulary terms."""
    return VocabularyHttp.terms(sys.modules[__name__], request)


async def api_lexicon_add_term(request: web.Request) -> web.Response:
    """POST /api/lexicon/terms {canonical, aliases?} — add a manual term."""
    return await VocabularyHttp.add(sys.modules[__name__], request, "term")


async def api_lexicon_update_term(request: web.Request) -> web.Response:
    """PATCH /api/lexicon/terms/{id} {enabled?} — enable/disable (prune) a term."""
    return await VocabularyHttp.update(
        sys.modules[__name__], request, "enabled", "set_enabled", "term not found"
    )


async def api_lexicon_delete_term(request: web.Request) -> web.Response:
    """DELETE /api/lexicon/terms/{id} — remove a term entirely."""
    return VocabularyHttp.delete(sys.modules[__name__], request)


async def api_lexicon_rebuild(request: web.Request) -> web.Response:
    """POST /api/lexicon/rebuild — resync graph-sourced terms from knowledge entities
    (upserts current ones, prunes graph terms whose entity left the graph)."""
    return VocabularyHttp.rebuild(sys.modules[__name__])


async def api_lexicon_corrections(request: web.Request) -> web.Response:
    """GET /api/lexicon/corrections — list learned corrections (most-corrected first)."""
    return VocabularyHttp.corrections(sys.modules[__name__])


async def api_lexicon_add_correction(request: web.Request) -> web.Response:
    """POST /api/lexicon/corrections {heard, meant, always?} — record a learned fix
    (LEX.5). Called by the Vocabulary UI + the Minutes transcript-edit flow."""
    return await VocabularyHttp.add(sys.modules[__name__], request, "correction")


async def api_lexicon_update_correction(request: web.Request) -> web.Response:
    """PATCH /api/lexicon/corrections/{id} {auto_apply} — toggle 'always fix this'."""
    return await VocabularyHttp.update(
        sys.modules[__name__],
        request,
        "auto_apply",
        "set_correction_auto_apply",
        "correction not found",
    )


async def api_lexicon_reset(request: web.Request) -> web.Response:
    """POST /api/lexicon/reset — drop all terms + corrections (rebuild repopulates graph).

    ``confirm: true`` is required. "Rebuild repopulates" holds only for terms DERIVED from the
    graph; the CORRECTIONS are user-authored — someone typed each one to teach the system a word
    it kept getting wrong — and no rebuild brings those back. So this is an unrecoverable wipe of
    hand-entered work, behind a verb ("reset") that does not read as one.
    """
    return await VocabularyHttp.reset(sys.modules[__name__], request)


def register_lexicon_routes(app: web.Application) -> None:
    """Register /api/lexicon/* — the Vocabulary panel + Minutes correction seam."""
    VocabularyHttp.routes(sys.modules[__name__], app)
