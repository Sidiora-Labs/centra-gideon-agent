from pathlib import Path

import pytest

from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.engine.agents.native.smart_case import LineQuery, ignores_case


@pytest.mark.parametrize(
    "pattern,fold",
    [
        (r"pick\Wup", True),
        (r"(?P<NAME>dentist)", True),
        (r"(?i:dentist)", True),
        (r"[A-Z]entist", False),
        (r"dentist\N{SPACE}", True),
        ("Dentist", False),
    ],
)
def test_regex_matched_letters(pattern, fold):
    assert ignores_case(pattern, regex=True) is fold


def test_unicode_plain_and_regex_line_anchors():
    assert LineQuery("strasse", regex=False).in_line("Straße")
    match = LineQuery("^dentist$", regex=True)
    assert match.in_file("header\nDentist\n") and match.in_line("Dentist")
    assert not match.in_line("other Dentist")


@pytest.mark.asyncio
async def test_native_search_case_overrides_filters_caps_and_admission(tmp_path):
    (tmp_path / "Appointments.MD").write_text("Dentist\nPick up\nDentist\n")
    (tmp_path / "lower.md").write_text("dentist\n")
    (tmp_path / "binary.md").write_bytes(b"\0Dentist")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hidden.md").write_text("Dentist")
    outside = tmp_path.parent / (tmp_path.name + "-outside.md")
    outside.write_text("Dentist secret")
    (tmp_path / "link.md").symlink_to(outside)
    p = NativeBuiltinToolProvider(tmp_path)

    async def run(name, **args):
        result = await p.invoke(name, args)
        assert result.success, result.error
        return result.output

    assert "Appointments.MD" in await run("glob", pattern="**/*.md")
    assert "Appointments.MD" not in await run(
        "glob", pattern="**/*.md", ignore_case="false"
    )
    assert "Appointments.MD" in await run("glob", pattern="**/*.MD", ignore_case="true")
    text = await run("grep", query="dentist", glob="**/*.md")
    assert "Appointments.MD" in text and "lower.md" in text
    assert all(
        name not in text for name in ("binary.md", "hidden.md", "link.md", "secret")
    )
    assert "lower.md" not in await run("grep", query="Dentist")
    assert "Appointments.MD" not in await run(
        "grep", query="dentist", ignore_case="false"
    )
    assert "Pick up" in await run("grep", query=r"pick\Wup", regex="true")
    assert "max_results=1" in await run("grep", query="dentist", max_results=1)
    invalid = await p.invoke("glob", {"pattern": "**/*", "ignore_case": "maybe"})
    assert not invalid.success
