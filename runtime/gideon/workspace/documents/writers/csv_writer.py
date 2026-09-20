"""Single-sheet SheetModel → UTF-8 CSV bytes."""

from __future__ import annotations

import csv
import io

from gideon.workspace.documents.model import SheetModel
from gideon.workspace.documents.registry import register_writer


def render_csv(model: object) -> bytes:
    if not isinstance(model, SheetModel):
        raise TypeError("csv writer expects a SheetModel")
    if len(model.sheets) != 1:
        raise ValueError("csv requires exactly one sheet")
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(model.sheets[0].rows)
    return output.getvalue().encode("utf-8")


register_writer("csv", render_csv)
