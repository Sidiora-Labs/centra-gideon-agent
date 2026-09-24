import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "agents" / "acp-parity.md"


def test_kiro_coverage_matches_the_recorded_closure():
    text = DOC.read_text(encoding="utf-8")
    rows = re.findall(r"^\| kiro-cli \| (.+)$", text, re.MULTILINE)
    assert len(rows) == 1
    cells = [value.strip().strip("*") for value in rows[0].split("|")]
    counts = tuple(int(value) for value in cells[:4])
    assert counts == (43, 19, 1, 0)
    assert sum(counts) == 63
    assert "Closed 2026-09-20" in cells[4]
    for observation in ("K2", "K60", "K100", "O123", "O189", "O190", "K47"):
        assert observation in cells[4]


def test_kiro_environment_count_has_a_named_nonverdict_source():
    text = DOC.read_text(encoding="utf-8")
    section = text.split("\n## kiro-cli\n", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if "| OS sandbox wrap:" in line]
    assert len(rows) == 1
    assert "`ENV`, not a verdict" in rows[0]
    assert "`K47`" in rows[0]
    assert "kiro-cli 2.18.1" in rows[0]


def test_kiro_zero_residual_keeps_the_closing_observations():
    text = DOC.read_text(encoding="utf-8")
    section = text.split("\n## kiro-cli\n", 1)[1].split("\n## ", 1)[0]
    residual = section.split("### Not yet measured (0 of 63 cells)", 1)[1]
    assert "`K55`" in residual
    assert "`K60`" in residual
    assert "`K47`" in residual
    assert "one `ENV` result" in residual
    assert "43 confirmed + 19 diverged + 1 environment-limited + 0" in text
    assert "unexercised = 63 cells" in text
