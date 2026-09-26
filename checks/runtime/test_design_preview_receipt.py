"""Canvas review follows the current visual artifact version."""

from gideon.automation.loop import files, store
from gideon.automation.loop.design_preview import all_current_reviewed, receipts, record
from gideon.automation.loop.loop import Loop
from gideon.workspace.artifacts.models import Artifact


def test_preview_review_is_durable_and_version_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "config_dir", lambda: tmp_path)
    loop_id = store.create(Loop(id="", name="Preview review", kind="design", task="Design a component")).id
    current = Artifact(slug="sample", name="Sample", kind="react", version=1)
    assert not all_current_reviewed(loop_id, [current])

    record(loop_id, current.slug, current.version, True)
    assert receipts(loop_id)[current.slug]["version"] == 1
    assert all_current_reviewed(loop_id, [current])
    assert not all_current_reviewed(loop_id, [Artifact(slug="sample", name="Sample", kind="react", version=2)])

    record(loop_id, current.slug, current.version, False)
    assert not all_current_reviewed(loop_id, [current])
