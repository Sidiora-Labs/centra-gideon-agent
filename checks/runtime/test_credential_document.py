import stat

from gideon.core.config.credentials import DotenvDocument, parse_dotenv


def test_upsert_preserves_noncredential_lines_and_replaces_every_duplicate(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# keep\n A = first \ninvalid line\nA=second\nB=untouched\n", encoding="utf-8"
    )
    document = DotenvDocument(path)
    document.upsert("A", "replacement")
    assert (
        path.read_text()
        == "# keep\nA=replacement\ninvalid line\nA=replacement\nB=untouched\n"
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert document.names() == ["A", "A", "B"]
    assert document.values() == {"A": "replacement", "B": "untouched"}


def test_remove_reports_actual_occurrences_and_preserves_other_content(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# keep\nA=one\nB=two\nA=three\n", encoding="utf-8")
    document = DotenvDocument(path)
    assert document.remove(["A", "absent"]) == ["A", "A"]
    assert path.read_text() == "# keep\nB=two\n"
    before = path.stat().st_mtime_ns
    assert document.remove(["absent"]) == []
    assert path.stat().st_mtime_ns == before


def test_read_repairs_permissions_and_keeps_empty_and_embedded_equals_values(tmp_path):
    path = tmp_path / ".env"
    path.write_text("EMPTY=\nCOMPOUND=a=b\n", encoding="utf-8")
    path.chmod(0o644)
    assert DotenvDocument(path).values() == {"EMPTY": "", "COMPOUND": "a=b"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert parse_dotenv(" # comment\n = empty-key\nT=  spaced  \n") == {
        "": "empty-key",
        "T": "spaced",
    }


def test_removing_last_credential_leaves_an_empty_private_file(tmp_path):
    path = tmp_path / ".env"
    document = DotenvDocument(path)
    document.upsert("ONE", "value")
    assert document.remove(["ONE"]) == ["ONE"]
    assert path.read_bytes() == b""
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
