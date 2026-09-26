"""Telegram MarkdownV2 renderer ported from AgentCore."""

import re

_MDV2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#\+\-=|{}.!\\])")


def _escape_mdv2(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters with a preceding backslash."""
    return _MDV2_ESCAPE_RE.sub(r"\\\1", text)


TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$")


def is_table_row(line: str) -> bool:
    """Return True if *line* could plausibly be a table data row."""
    stripped = line.strip()
    return bool(stripped) and "|" in stripped


def split_markdown_table_row(line: str) -> list[str]:
    """Split a GFM table row into stripped cell values.

    Thin delegate to the canonical implementation in
    :mod:`agent.markdown_tables` (``split_table_row``) so the three
    formerly byte-identical copies (here, ``agent/markdown_tables.py``,
    ``weixin._split_table_row``) share one body.
    """
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _render_table_block(table_block: list[str]) -> str:
    """Render a detected GFM table as bold-heading + bullet groups.

    Uses the same alignment logic as Telegram's renderer: for non-row-label
    tables, ``data_cells = cells`` (the full row) and the bullet whose value
    duplicates the heading is skipped.  This keeps header→value alignment
    correct.
    """
    if len(table_block) < 3:
        return "\n".join(table_block)

    headers = split_markdown_table_row(table_block[0])
    if len(headers) < 2:
        return "\n".join(table_block)

    first_data_row = (
        split_markdown_table_row(table_block[2]) if len(table_block) > 2 else []
    )
    has_row_label_col = len(first_data_row) == len(headers) + 1

    rendered_groups: list[str] = []
    for index, row in enumerate(table_block[2:], start=1):
        cells = split_markdown_table_row(row)
        if has_row_label_col:
            heading = cells[0] if cells and cells[0] else f"Row {index}"
            data_cells = cells[1:]
        else:
            heading = next((cell for cell in cells if cell), f"Row {index}")
            data_cells = cells

        if len(data_cells) < len(headers):
            data_cells.extend([""] * (len(headers) - len(data_cells)))
        elif len(data_cells) > len(headers):
            data_cells = data_cells[: len(headers)]

        bullets: list[str] = []
        for header, value in zip(headers, data_cells):
            if not has_row_label_col and value == heading:
                continue
            bullets.append(f"• {header}: {value}")

        group_lines = [f"**{heading}**", *bullets]
        rendered_groups.append("\n".join(group_lines))

    return "\n\n".join(rendered_groups)


def convert_table_to_bullets(text: str) -> str:
    """Rewrite GFM pipe tables into bold-heading + bullet groups.

    Tables inside fenced code blocks are left alone.
    """
    if "|" not in text or "-" not in text:
        return text

    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        if (
            "|" in line
            and i + 1 < len(lines)
            and TABLE_SEPARATOR_RE.match(lines[i + 1])
        ):
            table_block = [line, lines[i + 1]]
            j = i + 2
            while j < len(lines) and is_table_row(lines[j]):
                table_block.append(lines[j])
                j += 1
            out.append(_render_table_block(table_block))
            i = j
            continue

        out.append(line)
        i += 1

    return "\n".join(out)


_wrap_markdown_tables = convert_table_to_bullets


def to_markdown_v2(content: str) -> str:
    """
    Convert standard markdown to Telegram MarkdownV2 format.

    Protected regions (code blocks, inline code) are extracted first so
    their contents are never modified.  Standard markdown constructs
    (headers, bold, italic, links) are translated to MarkdownV2 syntax,
    and all remaining special characters are escaped.
    """
    if not content:
        return content

    placeholders: dict = {}
    counter = [0]

    def _ph(value: str) -> str:
        """Stash *value* behind a placeholder token that survives escaping."""
        key = f"\x00PH{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = value
        return key

    text = content

    # 0) Rewrite GFM-style pipe tables into Telegram-friendly row groups
    #    before the normal MarkdownV2 conversions run.
    text = _wrap_markdown_tables(text)

    # 1) Protect fenced code blocks (``` ... ```)
    #    Per MarkdownV2 spec, \ and ` inside pre/code must be escaped.
    def _protect_fenced(m):
        raw = m.group(0)
        # Split off opening ``` (with optional language) and closing ```
        open_end = raw.index("\n") + 1 if "\n" in raw[3:] else 3
        opening = raw[:open_end]
        body_and_close = raw[open_end:]
        body = body_and_close[:-3]
        body = body.replace("\\", "\\\\").replace("`", "\\`")
        return _ph(opening + body + "```")

    text = re.sub(
        r"(```(?:[^\n]*\n)?[\s\S]*?```)",
        _protect_fenced,
        text,
    )

    # 2) Protect inline code (`...`)
    #    Escape \ inside inline code per MarkdownV2 spec.
    text = re.sub(
        r"(`[^`]+`)",
        lambda m: _ph(m.group(0).replace("\\", "\\\\")),
        text,
    )

    # 3) Convert markdown links – escape the display text; inside the URL
    #    only ')' and '\' need escaping per the MarkdownV2 spec.
    def _convert_link(m):
        display = _escape_mdv2(m.group(1))
        url = m.group(2).replace("\\", "\\\\").replace(")", "\\)")
        return _ph(f"[{display}]({url})")

    text = re.sub(r"\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)", _convert_link, text)

    # 4) Convert markdown headers (## Title) → bold *Title*
    def _convert_header(m):
        inner = m.group(1).strip()
        # Strip redundant bold markers that may appear inside a header
        inner = re.sub(r"\*\*(.+?)\*\*", r"\1", inner)
        return _ph(f"*{_escape_mdv2(inner)}*")

    text = re.sub(r"^#{1,6}\s+(.+)$", _convert_header, text, flags=re.MULTILINE)

    # 5) Convert bold: **text** → *text* (MarkdownV2 bold)
    text = re.sub(
        r"\*\*(.+?)\*\*",
        lambda m: _ph(f"*{_escape_mdv2(m.group(1))}*"),
        text,
    )

    # 6) Convert italic: *text* (single asterisk) → _text_ (MarkdownV2 italic)
    #    [^*\n]+ prevents matching across newlines (which would corrupt
    #    bullet lists using * markers and multi-line content).
    text = re.sub(
        r"\*([^*\n]+)\*",
        lambda m: _ph(f"_{_escape_mdv2(m.group(1))}_"),
        text,
    )

    # 7) Convert strikethrough: ~~text~~ → ~text~ (MarkdownV2)
    text = re.sub(
        r"~~(.+?)~~",
        lambda m: _ph(f"~{_escape_mdv2(m.group(1))}~"),
        text,
    )

    # 8) Convert spoiler: ||text|| → ||text|| (protect from | escaping)
    text = re.sub(
        r"\|\|(.+?)\|\|",
        lambda m: _ph(f"||{_escape_mdv2(m.group(1))}||"),
        text,
    )

    # 9) Convert blockquotes: > at line start → protect > from escaping
    #    Handle both regular blockquotes (> text) and expandable blockquotes
    #    (Telegram MarkdownV2: **> for expandable start, || to end the quote)
    def _convert_blockquote(m):
        prefix = m.group(1)  # >, >>, >>>, **>, or **>> etc.
        content = m.group(2)
        # Check if content ends with || (expandable blockquote end marker)
        # In this case, preserve the trailing || unescaped for Telegram
        if prefix.startswith("**") and content.endswith("||"):
            return _ph(f"{prefix} {_escape_mdv2(content[:-2])}||")
        return _ph(f"{prefix} {_escape_mdv2(content)}")

    text = re.sub(
        r"^((?:\*\*)?>{1,3}) (.+)$",
        _convert_blockquote,
        text,
        flags=re.MULTILINE,
    )

    # 10) Escape remaining special characters in plain text
    text = _escape_mdv2(text)

    # 11) Restore placeholders in reverse insertion order so that
    #    nested references (a placeholder inside another) resolve correctly.
    for key in reversed(list(placeholders.keys())):
        text = text.replace(key, placeholders[key])

    # 12) Safety net: escape unescaped ( ) { } that slipped through
    #     placeholder processing.  Split the text into code/non-code
    #     segments so we never touch content inside ``` or ` spans.
    _code_split = re.split(r"(```[\s\S]*?```|`[^`]+`)", text)
    _safe_parts = []
    for _idx, _seg in enumerate(_code_split):
        if _idx % 2 == 1:
            # Inside code span/block — leave untouched
            _safe_parts.append(_seg)
        else:
            # Outside code — escape bare ( ) { }
            def _esc_bare(m, _seg=_seg):
                s = m.start()
                ch = m.group(0)
                # Already escaped
                if s > 0 and _seg[s - 1] == "\\":
                    return ch
                # ( that opens a MarkdownV2 link [text](url)
                if ch == "(" and s > 0 and _seg[s - 1] == "]":
                    return ch
                # ) that closes a link URL
                if ch == ")":
                    before = _seg[:s]
                    if "](http" in before or "](" in before:
                        # Check depth
                        depth = 0
                        for j in range(s - 1, max(s - 2000, -1), -1):
                            if _seg[j] == "(":
                                depth -= 1
                                if depth < 0:
                                    if j > 0 and _seg[j - 1] == "]":
                                        return ch
                                    break
                            elif _seg[j] == ")":
                                depth += 1
                return "\\" + ch

            _safe_parts.append(re.sub(r"[(){}]", _esc_bare, _seg))
    text = "".join(_safe_parts)

    return text


def split_text(text: str, limit: int = 3500) -> list[str]:
    parts = []
    current = ""
    units = 0
    for char in text:
        width = len(char.encode("utf-16-le")) // 2
        if units + width > limit:
            parts.append(current)
            current = ""
            units = 0
        current += char
        units += width
    if current:
        parts.append(current)
    return parts
