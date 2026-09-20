from __future__ import annotations

import csv
import io

import pytest

from gideon.integrations.mcp_artifacts import _document_create
from gideon.workspace.artifacts.models import is_binary_kind
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.documents import available_formats, get_writer
from gideon.workspace.documents.model import DocumentModel, SheetModel


def test_csv_writer_quotes_and_requires_one_sheet():
    writer = get_writer("csv")
    data = writer(SheetModel.from_rows({"Only": [["name", "note"], ["A", "x,y"]]}))
    assert list(csv.reader(io.StringIO(data.decode()))) == [
        ["name", "note"],
        ["A", "x,y"],
    ]
    assert "csv" in available_formats()
    assert not is_binary_kind("csv")
    with pytest.raises(TypeError, match="SheetModel"):
        writer(DocumentModel())
    with pytest.raises(ValueError, match="exactly one sheet"):
        writer(SheetModel.from_rows({"A": [[1]], "B": [[2]]}))


def test_sheet_create_stores_csv_as_versioned_text_and_refuses_wrong_shapes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    prov = NativeArtifactProvider(root=tmp_path / "artifacts")

    def audit(outcome, slug="", error=""):
        return None

    reply = _document_create(
        prov,
        "sheet_create",
        {
            "name": "People",
            "format": "csv",
            "rows": [["name", "note"], ["A", "x,y"]],
        },
        None,
        audit,
    )
    assert reply.startswith("Created csv:") and "Open at" in reply
    art = prov.list()[0]
    assert art.kind == "csv"
    assert prov.get(art.slug).content == 'name,note\nA,"x,y"\n'

    updated = _document_create(
        prov,
        "sheet_create",
        {
            "name": "People",
            "format": "csv",
            "slug": art.slug,
            "csv": 'name,note\nB,"z,w"\n',
        },
        None,
        audit,
    )
    assert updated.startswith("Updated csv:")
    assert prov.get(art.slug).version == 2
    assert 'B,"z,w"' in prov.get(art.slug).content

    multi = _document_create(
        prov,
        "sheet_create",
        {"name": "No", "format": "csv", "sheets": {"A": [[1]], "B": [[2]]}},
        None,
        audit,
    )
    prose = _document_create(
        prov,
        "document_create",
        {"name": "No", "format": "csv", "markdown": "prose"},
        None,
        audit,
    )
    assert "exactly one sheet" in multi
    assert "use sheet_create" in prose
