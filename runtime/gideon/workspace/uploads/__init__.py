"""Upload subsystem: one filetype-keyed size policy + the resumable transfer store.

Every upload surface (chat attach, Files browser, Knowledge ingest, workspace)
routes its size gate through :mod:`gideon.workspace.uploads.policy` so per-filetype
limits are consistent and centrally tunable, and its bytes through the resumable
protocol in :mod:`gideon.workspace.uploads.store` for anything above the single-POST
threshold.
"""

from gideon.workspace.uploads.policy import (
    UPLOAD_CATEGORIES,
    UploadCheck,
    category_for,
    check_upload,
    limit_for_category,
    limits_table,
    single_post_threshold,
)

__all__ = [
    "UPLOAD_CATEGORIES",
    "UploadCheck",
    "category_for",
    "check_upload",
    "limit_for_category",
    "limits_table",
    "single_post_threshold",
]
