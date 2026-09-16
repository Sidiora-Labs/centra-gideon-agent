"""The run/style/cell half of the document model — derivation, precedence, neutrality.

`checks/runtime/test_documents.py` proves the pre-existing model still behaves; this file proves the
additive fields do what they claim, that they never overwrite an author's explicit value,
and that the module stayed free of format-specific vocabulary.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from gideon.workspace.documents import (
    Block,
    Cell,
    DocumentModel,
    PageSetup,
    ParagraphStyle,
    Run,
)
from gideon.workspace.documents import model as model_module
from gideon.workspace.documents.model import ALIGNMENTS, BLOCK_KINDS, ORIENTATIONS


def test_runs_only_block_answers_text() -> None:
    block = Block(
        kind="paragraph",
        runs=[Run(text="plain "), Run(text="bold", bold=True), Run(text=" tail")],
    )
    assert block.text == "plain bold tail"
    assert [run.bold for run in block.runs] == [False, True, False]


def test_cells_only_table_answers_rows() -> None:
    block = Block(
        kind="table",
        cells=[
            [Cell(runs=[Run(text="Region")], bold=True), Cell(text="Total", bold=True)],
            [Cell(text="EU"), Cell(runs=[Run(text="1"), Run(text="2")])],
        ],
    )
    assert block.rows == [["Region", "Total"], ["EU", "12"]]


def test_explicit_text_beside_runs_wins() -> None:
    block = Block(
        kind="paragraph",
        text="see the docs <https://example.invalid>",
        runs=[Run(text="see the "), Run(text="docs", link="https://example.invalid")],
    )
    assert block.text == "see the docs <https://example.invalid>"
    assert block.runs[1].link == "https://example.invalid"
    assert "".join(run.text for run in block.runs) != block.text


def test_explicit_rows_beside_cells_wins() -> None:
    block = Block(
        kind="table",
        rows=[["header"], ["explicit"]],
        cells=[[Cell(text="derived")]],
    )
    assert block.rows == [["header"], ["explicit"]]
    assert block.cells[0][0].text == "derived"
    assert [[cell.text for cell in row] for row in block.cells] != block.rows


def test_cell_derives_its_own_text_but_never_clobbers_it() -> None:
    assert Cell(runs=[Run(text="a"), Run(text="b")]).text == "ab"
    cell = Cell(text="kept", runs=[Run(text="ignored")])
    assert cell.text == "kept"
    assert "".join(run.text for run in cell.runs) != cell.text


def test_empty_runs_list_leaves_text_exactly_as_passed() -> None:
    assert Block(kind="paragraph", runs=[]).text == ""
    assert Block(kind="paragraph", text="", runs=[]).text == ""
    assert Block(kind="paragraph", text="held", runs=[]).text == "held"
    assert Cell(runs=[]).text == ""
    assert Cell(text="held", runs=[]).text == "held"
    assert Block(kind="table", cells=[]).rows == []
    assert Block(kind="table", rows=[["held"]], cells=[]).rows == [["held"]]


@pytest.mark.parametrize("cls", [Run, Cell, ParagraphStyle, PageSetup])
def test_style_dataclass_defaults_are_all_falsy(cls: type) -> None:
    for spec in dataclasses.fields(cls):
        if spec.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            default = spec.default_factory()  # type: ignore[misc]
        else:
            default = spec.default
        assert (
            default is not dataclasses.MISSING
        ), f"{cls.__name__}.{spec.name} has no default"
        assert (
            not default
        ), f"{cls.__name__}.{spec.name} defaults to {default!r}, not a falsy value"


def test_document_model_page_defaults_to_none_and_block_style_too() -> None:
    assert DocumentModel().page is None
    assert Block(kind="paragraph").style is None


def test_appended_fields_keep_positional_construction_working() -> None:
    block = Block("heading", "Title", 2)
    assert (block.kind, block.text, block.level) == ("heading", "Title", 2)
    assert (block.runs, block.cells, block.style) == ([], [], None)


def test_zero_floats_are_distinguishable_from_an_explicit_small_value() -> None:
    unset = ParagraphStyle()
    tight = ParagraphStyle(space_before_pt=0.5, space_after_pt=0.5, line_spacing=0.9)
    assert (unset.space_before_pt, unset.space_after_pt, unset.line_spacing) == (
        0.0,
        0.0,
        0.0,
    )
    assert unset.line_spacing != tight.line_spacing
    assert unset.space_before_pt != tight.space_before_pt
    assert not unset.line_spacing
    assert tight.line_spacing
    assert PageSetup().margin_left_pt == 0.0
    assert PageSetup(margin_left_pt=18.0).margin_left_pt == 18.0
    assert PageSetup(margin_left_pt=18.0).margin_left_pt != PageSetup().margin_left_pt


@pytest.mark.parametrize("align", ALIGNMENTS)
def test_every_declared_alignment_is_accepted(align: str) -> None:
    assert ParagraphStyle(align=align).align == align
    assert Cell(align=align).align == align


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_every_declared_orientation_is_accepted(orientation: str) -> None:
    assert PageSetup(orientation=orientation).orientation == orientation


def test_unknown_alignment_raises_rather_than_normalising() -> None:
    with pytest.raises(ValueError, match="unknown alignment"):
        ParagraphStyle(align="centre")
    with pytest.raises(ValueError, match="unknown alignment"):
        Cell(align="middle")
    with pytest.raises(ValueError, match="unknown alignment"):
        Block(kind="paragraph", style=ParagraphStyle(align="LEFT"))


def test_unknown_orientation_raises_rather_than_normalising() -> None:
    with pytest.raises(ValueError, match="unknown orientation"):
        PageSetup(orientation="landscpae")
    with pytest.raises(ValueError, match="unknown orientation"):
        DocumentModel(page=PageSetup(orientation="sideways"))


def test_block_kinds_is_non_empty() -> None:
    assert BLOCK_KINDS
    assert "paragraph" in BLOCK_KINDS


def test_all_empty_runs_derive_empty_string_rather_than_raising() -> None:
    assert Block(kind="paragraph", runs=[Run(), Run()]).text == ""
    assert Block(kind="paragraph", runs=[Run(text="")]).text == ""
    assert Cell(runs=[Run(), Run()]).text == ""
    assert Block(kind="table", cells=[[], [Cell()]]).rows == [[], [""]]


FORMAT_VOCABULARY = ("docx", "pptx", "xlsx", "w:", "rpr", "ooxml")


def _code_below_module_docstring(source: str) -> str:
    """Everything after the module docstring.

    The module docstring is where the guardrail is *declared*, so it legitimately names the
    vocabulary it forbids; scanning it would make the rail unfalsifiable-by-construction.
    Everything below it — code, comments and class docstrings alike — must stay neutral.
    """
    first = ast.parse(source).body[0]
    if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)):
        return source
    return "\n".join(source.splitlines()[first.end_lineno or 0 :])


def _format_vocabulary_hits(source: str) -> list[str]:
    haystack = _code_below_module_docstring(source).lower()
    return [token for token in FORMAT_VOCABULARY if token in haystack]


def test_model_module_names_no_file_format_vocabulary() -> None:
    source = Path(model_module.__file__).read_text(encoding="utf-8")
    body = _code_below_module_docstring(source)
    assert "BLOCK_KINDS" in body
    assert "class Run" in body
    assert len(body) > 0.5 * len(source)

    assert _format_vocabulary_hits(source) == []


def test_the_vocabulary_rail_can_actually_fail() -> None:
    offender = '"""Module docstring."""\n\nWRITER = "the .docx writer"\n'
    assert _format_vocabulary_hits(offender) == ["docx"]

    source = Path(model_module.__file__).read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source)) or ""
    assert any(token in docstring.lower() for token in FORMAT_VOCABULARY)
