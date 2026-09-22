from gideon.automation.loop.kinds.sdlc import _command_runnable_here


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
        result = _command_runnable_here(f"{keyword} pytest", str(tmp_path))
        assert result.runnable is True
        assert result.missing_binary == ""


def test_absolute_executable_must_exist(tmp_path):
    executable = tmp_path / "pytest"
    missing = _command_runnable_here(f"{executable} -q", str(tmp_path))
    assert missing.runnable is False
    assert missing.missing_binary == str(executable)

    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    assert _command_runnable_here(f"{executable} -q", str(tmp_path)).runnable is True
