from gideon.automation.loop.kinds.sdlc import _command_runnable_here, _command_word


def test_leading_shell_keywords_are_syntax_not_commands(tmp_path):
    for keyword in (
        "if",
        "then",
        "else",
        "elif",
        "fi",
        "for",
        "while",
        "until",
        "do",
        "done",
        "case",
        "esac",
        "in",
        "function",
        "select",
        "time",
        "coproc",
        "!",
        "{",
        "}",
    ):
        assert _command_word(f"{keyword} pytest") == ""
        assert _command_runnable_here(f"{keyword} pytest", str(tmp_path)) is True


def test_absolute_executable_uses_its_command_name(tmp_path):
    assert _command_word("/usr/local/bin/pytest -q") == "pytest"
    assert _command_runnable_here("/usr/local/bin/pytest -q", str(tmp_path)) is False
    (tmp_path / "pyproject.toml").touch()
    assert _command_runnable_here("/usr/local/bin/pytest -q", str(tmp_path)) is True
