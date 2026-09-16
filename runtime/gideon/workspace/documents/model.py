"""The declarative document models — vendor-neutral by construction.

No OOXML vocabulary lives here: these describe *what* a document contains, and a writer
decides how to express it in its format. That split is what lets a second format reuse
the same model, and what keeps the agent from having to know anything about file formats.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

BLOCK_KINDS = (
    "heading",
    "paragraph",
    "bullets",
    "numbered",
    "table",
    "image",
    "pagebreak",
    "code",
)

ALIGNMENTS = ("", "left", "center", "right", "justify")

ORIENTATIONS = ("", "portrait", "landscape")

PAGE_SIZES = ("", "letter", "a4", "legal", "tabloid")

PAGE_SIZE_IN: dict[str, tuple[float, float]] = {
    "letter": (8.5, 11.0),
    "a4": (210 / 25.4, 297 / 25.4),
    "legal": (8.5, 14.0),
    "tabloid": (11.0, 17.0),
}


@dataclass
class Run:
    """A stretch of text with uniform character formatting.

    Formatting is described, not encoded: `bold` is a property of the document, so it
    survives into any format a writer knows how to emit.
    """

    text: str = ""
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: str = ""


@dataclass
class ParagraphStyle:
    """Optional paragraph-level presentation. Every field's zero value means "unset"."""

    align: str = ""
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    line_spacing: float = 0.0
    indent_left_pt: float = 0.0
    indent_right_pt: float = 0.0
    first_line_indent_pt: float = 0.0
    keep_with_next: bool = False

    def __post_init__(self) -> None:
        if self.align not in ALIGNMENTS:
            raise ValueError(
                f"unknown alignment {self.align!r}; expected one of {ALIGNMENTS}"
            )


@dataclass
class PageSetup:
    """Optional page-level presentation. Zero values mean "unset", as in ParagraphStyle.

    **Margins are per edge, not one number.** A single margin could not express the
    asymmetric top/bottom vs. left/right geometry that Word's own default template ships,
    so every document built from it parsed as lossy — the loss report fired on documents
    this project generated itself. Four fields make that geometry representable.
    """

    size: str = ""
    orientation: str = ""
    margin_top_pt: float = 0.0
    margin_bottom_pt: float = 0.0
    margin_left_pt: float = 0.0
    margin_right_pt: float = 0.0
    header_text: str = ""
    footer_text: str = ""
    page_numbers: bool = False

    def __post_init__(self) -> None:
        if self.orientation not in ORIENTATIONS:
            raise ValueError(
                f"unknown orientation {self.orientation!r}; expected one of {ORIENTATIONS}"
            )
        if self.size not in PAGE_SIZES:
            raise ValueError(
                f"unknown page size {self.size!r}; expected one of {PAGE_SIZES}"
            )

    def size_in(self) -> tuple[float, float]:
        """The page's (width, height) in inches, orientation applied.

        `(0.0, 0.0)` when no size is named — the writer's template decides, and inventing
        Letter here would silently reformat a document that never asked for one.
        """
        if not self.size:
            return (0.0, 0.0)
        width, height = PAGE_SIZE_IN[self.size]
        return (height, width) if self.orientation == "landscape" else (width, height)


@dataclass
class Cell:
    """One table cell: either formatted runs, or a plain display string, or both."""

    runs: list[Run] = field(default_factory=list)
    text: str = ""
    bold: bool = False
    align: str = ""

    def __post_init__(self) -> None:
        if self.align not in ALIGNMENTS:
            raise ValueError(
                f"unknown alignment {self.align!r}; expected one of {ALIGNMENTS}"
            )
        if self.runs and not self.text:
            self.text = "".join(run.text for run in self.runs)


@dataclass
class Block:
    """One flow element of a text document."""

    kind: str
    text: str = ""
    level: int = 1
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    artifact_slug: str = ""
    runs: list[Run] = field(default_factory=list)
    cells: list[list[Cell]] = field(default_factory=list)
    style: ParagraphStyle | None = None

    def __post_init__(self) -> None:
        if self.kind not in BLOCK_KINDS:
            raise ValueError(
                f"unknown block kind {self.kind!r}; expected one of {BLOCK_KINDS}"
            )
        self.level = max(1, min(6, int(self.level or 1)))
        if self.runs and not self.text:
            self.text = "".join(run.text for run in self.runs)
        if self.cells and not self.rows:
            self.rows = [[cell.text for cell in row] for row in self.cells]


@dataclass
class DocumentModel:
    """A flowing text document: a title plus ordered blocks."""

    title: str = ""
    blocks: list[Block] = field(default_factory=list)
    page: PageSetup | None = None


@dataclass
class SheetCell:
    """One spreadsheet cell: a typed value, an optional formula, optional presentation.

    **`value` and `formula` are separate fields, and that separation IS the fidelity.**
    A spreadsheet cell holds either a literal or a computed expression, and the two are
    different things in the file format — so a model that carried only a value would have
    to guess from the text which one it had. Guessing gets it wrong in both directions:
    ``"=SUM(A1)"`` typed as a value silently becomes a formula, and a literal label a user
    typed as ``"=WORK IN PROGRESS"`` silently becomes a broken one (Excel shows
    ``#NAME?``). Declaring the intent means neither can happen.

    `formula` keeps its leading ``=`` because that is how a person writes it, how the
    file stores it, and how every spreadsheet UI shows it — stripping it here would mean
    re-adding it in the writer, the parser, and the editor, three places to disagree.
    """

    value: object = None
    formula: str = ""
    number_format: str = ""
    bold: bool = False
    italic: bool = False
    font_color: str = ""
    fill: str = ""
    align: str = ""

    def __post_init__(self) -> None:
        if self.align not in ALIGNMENTS:
            raise ValueError(
                f"unknown alignment {self.align!r}; expected one of {ALIGNMENTS}"
            )
        if self.formula and not self.formula.startswith("="):
            raise ValueError(
                f"formula {self.formula!r} must start with '='; "
                "a literal that merely looks like one belongs in `value`"
            )

    @property
    def display(self) -> object:
        """What a reader sees: the formula if there is one, else the literal.

        The formula wins because that is what a spreadsheet's formula bar shows and what
        a plain-text export of the sheet should say — the cached value is a snapshot that
        may already be stale.
        """
        return self.formula or self.value


@dataclass
class Sheet:
    """One named sheet: a rectangle of cells plus the geometry Excel stores beside them."""

    name: str = ""
    cells: list[list[SheetCell]] = field(default_factory=list)
    column_widths: list[float] = field(default_factory=list)
    merges: list[str] = field(default_factory=list)
    frozen_header: bool = False

    @property
    def rows(self) -> list[list[object]]:
        """The plain display view — what a writer that knows nothing of formats emits.

        A property, not a stored mirror field: a stored copy of the cells is a second
        representation of the same data, and the first edit that updated one and not the
        other would silently write stale content. Derived means it cannot go stale.
        """
        return [[cell.display for cell in row] for row in self.cells]


@dataclass
class SheetModel:
    """A workbook: ordered named sheets.

    Ordered (a list, not a dict keyed by name) because sheet order is part of the
    document, and because a `Sheet` already carries its own name — a dict would make the
    name two things that can disagree.
    """

    sheets: list[Sheet] = field(default_factory=list)

    @classmethod
    def from_rows(
        cls, rows_by_name: Mapping[str, Sequence[Sequence[object]]]
    ) -> SheetModel:
        """Build a plain, unformatted workbook from ``{name: rows}``.

        The convenience form for a caller that has data and no opinion about
        presentation (the agent's ``create_artifact`` path). A raw value that looks like
        a formula stays a LITERAL here: promoting it would be exactly the guess
        :class:`SheetCell` exists to avoid, and a caller that wants a formula says so by
        building the cell.

        Row 0 is the header by this constructor's contract, so it is marked bold and
        frozen HERE rather than in the writer. The model is the whole story: a writer
        that invented a bold row would be presentation the model could not see, and the
        editor would show a header that looks plain and saves back plain.
        """
        return cls(
            sheets=[
                Sheet(
                    name=str(name),
                    cells=[
                        [SheetCell(value=value, bold=index == 0) for value in row]
                        for index, row in enumerate(rows)
                    ],
                    frozen_header=bool(rows),
                )
                for name, rows in rows_by_name.items()
            ]
        )


DECK_LAYOUTS = (
    "Title Slide",
    "Title and Content",
    "Section Header",
    "Two Content",
    "Comparison",
    "Title Only",
    "Blank",
    "Content with Caption",
    "Picture with Caption",
    "Title and Vertical Text",
    "Vertical Title and Text",
)

MAX_BULLET_LEVEL = 8


@dataclass
class Bullet:
    """One line of a slide's body, with its indent DEPTH.

    Depth is a field of the content, not a rendering flourish: an outline whose
    sub-points all flatten to the top level says something different from what its
    author wrote. Before this, a slide body was `list[str]` and the writer pinned
    ``level = 0``, so every deck came out flat no matter what went in.
    """

    text: str = ""
    level: int = 0

    def __post_init__(self) -> None:
        self.level = max(0, min(MAX_BULLET_LEVEL, int(self.level)))


@dataclass
class ShapeBox:
    """Where one of a slide's shapes sits, in inches from the top-left of the slide.

    All zeros means "wherever the layout puts it" — an inherited position, which is the
    normal case and the one a writer must not pin, or every re-render would freeze a
    shape at whatever the template happened to say the day the file was parsed.

    Inches, like `PageSetup.margin_in`, because that is the unit the editor shows and a
    person reasons in; the format's EMU are a writer's business.
    """

    left_in: float = 0.0
    top_in: float = 0.0
    width_in: float = 0.0
    height_in: float = 0.0

    @property
    def placed(self) -> bool:
        """True when this box overrides the layout's own position.

        Keyed on the SIZE, not on left/top: a shape flush against the top-left corner has
        `left_in == top_in == 0.0` and is still placed, while a box with no width is not a
        position at all.
        """
        return self.width_in > 0 and self.height_in > 0


@dataclass
class Slide:
    """One slide: a title, an outline body, speaker notes, and where its shapes sit."""

    title: str = ""
    bullets: list[Bullet] = field(default_factory=list)
    notes: str = ""
    artifact_slug: str = ""
    layout: str = ""
    title_box: ShapeBox = field(default_factory=ShapeBox)
    body_box: ShapeBox = field(default_factory=ShapeBox)

    def __post_init__(self) -> None:
        if self.layout and self.layout not in DECK_LAYOUTS:
            raise ValueError(
                f"unknown layout {self.layout!r}; expected one of {DECK_LAYOUTS}"
            )

    @classmethod
    def outline(
        cls, title: str = "", lines: Sequence[str] = (), *, notes: str = ""
    ) -> Slide:
        """A slide from plain lines, all at the top level.

        The plain constructor for a caller that has text and no opinion about depth —
        `SheetModel.from_rows`'s role for a deck. It exists so "I have five bullets"
        never becomes a reason to reach past `Bullet` and re-invent a flat body.
        """
        return cls(
            title=title, bullets=[Bullet(text=str(line)) for line in lines], notes=notes
        )


@dataclass
class DeckModel:
    """A deck: an optional cover title plus its slides, at a given slide size."""

    title: str = ""
    slides: list[Slide] = field(default_factory=list)
    width_in: float = 0.0
    height_in: float = 0.0
