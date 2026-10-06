"""The run deliverable reader must confine reads to declared roots."""

from gideon.automation.workflows.deliverable import (
    NOT_WRITTEN,
    ROOT_RUN_DIR,
    Root,
    Roots,
    read_document,
)

SECRET = "sk-ant-api03-" + ("A" * 20) + ("B" * 20) + ("C" * 15)


def test_traversal_and_symlink_targets_outside_run_root_are_not_read(tmp_path):
    run_root = tmp_path / "run"
    run_root.mkdir()
    outside = tmp_path / "outside-private.md"
    outside.write_text(SECRET, encoding="utf-8")
    (run_root / "linked.md").symlink_to(outside)
    (run_root / "REPORT.md").write_text(f"Run result: {SECRET}", encoding="utf-8")
    roots = Roots([Root(ROOT_RUN_DIR, str(run_root), True)])

    traversed = read_document(roots, "../outside-private.md")
    linked = read_document(roots, "linked.md")
    declared = read_document(roots, "REPORT.md")

    assert traversed.present is False
    assert linked.present is False
    assert traversed.absent_reason == NOT_WRITTEN
    assert linked.absent_reason == NOT_WRITTEN
    assert SECRET not in str(traversed.to_dict())
    assert SECRET not in str(linked.to_dict())
    assert declared.present is True
    assert "[REDACTED: credential]" in declared.content
    assert SECRET not in declared.content
