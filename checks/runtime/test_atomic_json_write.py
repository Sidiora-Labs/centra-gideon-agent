import json
import stat

from gideon.core import atomic_write


def test_json_writer_preserves_file_mode_and_notifies_subscribers(tmp_path):
    target = tmp_path / "document.json"
    target.write_text('{"before": true}\n', encoding="utf-8")
    target.chmod(0o640)
    observed = []

    def record(path):
        observed.append(path)

    atomic_write.register_post_write_hook(record)
    try:
        atomic_write.atomic_json_write(target, {"after": [1, 2]})
    finally:
        atomic_write.unregister_post_write_hook(record)

    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.read_text(encoding="utf-8") == json.dumps({"after": [1, 2]}, indent=2) + "\n"
    assert observed == [target]
    assert list(tmp_path.iterdir()) == [target]
