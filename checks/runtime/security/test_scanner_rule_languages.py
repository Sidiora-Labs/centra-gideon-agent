from __future__ import annotations

from pathlib import Path

from gideon.security.supply_chain import default_scanner


def test_python_exec_rule_does_not_match_javascript_regexp_exec(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "worker.js").write_text("const m = /x/.exec(value);\n", encoding="utf-8")
    js_report = default_scanner.scan(staged)
    assert "python_exec" not in {f.rule for f in js_report.findings}

    (staged / "worker.py").write_text("exec(source)\n", encoding="utf-8")
    py_report = default_scanner.scan(staged)
    assert "python_exec" in {f.rule for f in py_report.findings}
