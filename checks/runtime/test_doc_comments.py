import json

from gideon.interfaces.dashboard import doc_comments
from gideon.operations.durability.inventory import INVENTORY


def test_comment_store_crud_and_inventory(monkeypatch, tmp_path):
    monkeypatch.setattr(doc_comments.config_loader, "config_dir", lambda: tmp_path)
    created = doc_comments.create_comment(
        {"docId": "a", "docLabel": "A", "quote": "q", "comment": "first"}
    )
    assert created["id"].startswith("c-") and isinstance(created["ts"], int)
    assert doc_comments.update_comment(created["id"], "second")["comment"] == "second"
    assert doc_comments.delete_comments({created["id"]}) == 1
    assert json.loads((tmp_path / "doc_comments.json").read_text()) == []
    assert any(entry.path == "doc_comments.json" for entry in INVENTORY)
