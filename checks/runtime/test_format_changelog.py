import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from tooling.scripts.format_changelog import (
    FormatError,
    entry_type,
    format_changelog,
    main,
)


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*args, day=None):
        environment = dict(
            os.environ,
            GIT_AUTHOR_NAME="Example Contributor",
            GIT_COMMITTER_NAME="Example Committer",
        )
        if day:
            environment.update(GIT_AUTHOR_DATE=day, GIT_COMMITTER_DATE=day)
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], env=environment, text=True
        ).strip()

    run("init", "-q", "-b", "main")
    run("config", "user.name", "Example Contributor")
    run("config", "user.email", "example@example.invalid")
    commits = []
    for subject, day in (
        ("fix: Keep the first value", "2026-01-02T23:30:00-08:00"),
        ("An ordinary subject", "2026-01-03T00:15:00+02:00"),
    ):
        run("commit", "--allow-empty", "-q", "-m", subject, day=day)
        commits.append(run("rev-parse", "HEAD"))
    return repo, commits


def link(commit):
    return f"([{commit[:7]}](https://example.invalid/project/commit/{commit}))"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_real_authored_dates_preserve_prose_multiline_and_idempotence(history, newline):
    repo, commits = history
    source = (
        "# Changelog\n\nIntroductory prose.\n\n## [1.0.0] - 2026-01-04\n\n"
        "### Highlights\n\nKeep this prose and its list.\n\n- Human-readable highlight\n\n"
        f"### Other\n- An ordinary subject {link(commits[1])}\n"
        f"- fix: Keep the first value {link(commits[0])}\n"
        "  First continuation.\n\n  Second continuation.\n\nFinal release note.\n"
    ).replace("\n", newline)
    formatted = format_changelog(source, repo)
    assert "### 2026-01-03" in formatted
    assert "### 2026-01-02" in formatted
    assert "#### Bug fixes" in formatted
    assert formatted.index("### 2026-01-03") < formatted.index("### 2026-01-02")
    assert (
        f'- "Example Contributor" contributor-inferred 2026-01-03 main-inferred {commits[1]} "An ordinary subject" {link(commits[1])}'
        in formatted
    )
    assert (
        "First continuation." + newline + newline + "  Second continuation."
        in formatted
    )
    assert (
        "Keep this prose and its list."
        + newline
        + newline
        + "- Human-readable highlight"
        in formatted
    )
    assert "Final release note." in formatted
    assert "## [1.0.0] - 2026-01-04" + newline in formatted
    assert format_changelog(formatted, repo) == formatted


def test_structured_metadata_is_literal_and_ordinary_metadata_is_not_invented(history):
    repo, commits = history
    subject = (
        "Builder task-1 2026-10-02 main " + "a" * 40 + r" \"Fix a quoted subject\""
    )
    source = f"## [1]\n\n### Other\n- {subject} {link(commits[0])}\n"
    formatted = format_changelog(source, repo)
    assert f"- {subject} {link(commits[0])}" in formatted
    assert "### 2026-10-02" in formatted
    assert "#### Bug fixes" in formatted
    assert format_changelog(formatted, repo) == formatted


def test_unsupported_entries_and_missing_commits_fail_clearly(history):
    repo, _ = history
    with pytest.raises(FormatError, match="without a supported commit link"):
        format_changelog("## [1]\n\n### Other\n- Cannot infer this entry\n", repo)
    with pytest.raises(FormatError, match="Git lookup failed"):
        format_changelog(f"## [1]\n\n### Other\n- Missing {link('f' * 40)}\n", repo)


def test_cli_preview_check_output_backup_and_reviewed_source_guard(
    history, tmp_path, capsys
):
    repo, commits = history
    path = repo / "CHANGELOG.md"
    original = f"## [1]\n\n### Other\n- An ordinary subject {link(commits[1])}\n"
    path.write_text(original)
    arguments = [str(path), "--repo", str(repo)]
    assert main(arguments) == 0
    preview = capsys.readouterr().out
    assert path.read_text() == original
    assert main(arguments + ["--check"]) == 1
    output = tmp_path / "preview.md"
    assert main(arguments + ["--output", str(output)]) == 0
    assert output.read_text() == preview
    assert main(arguments + ["--output", str(path)]) == 2
    assert path.read_text() == original
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(original + "\nA new release note.\n")
    backup = tmp_path / "private" / "before.md"
    assert (
        main(
            arguments
            + ["--write", "--backup", str(backup), "--expected-sha256", digest]
        )
        == 2
    )
    assert not backup.exists()
    changed = path.read_bytes()
    assert main(arguments + ["--write", "--backup", str(backup)]) == 0
    assert backup.read_bytes() == changed
    assert backup.stat().st_mode & 0o777 == 0o600
    assert main(arguments + ["--check"]) == 0
    assert main(arguments + ["--write", "--backup", str(backup)]) == 2


def test_compact_mode_preserves_original_quoted_subject_and_is_idempotent(history):
    repo, commits = history
    source = f'## [1]\n\n### Other\n- "Quoted original subject" {link(commits[0])}\n'
    result = format_changelog(source, repo, "compact")
    assert '- "\\"Quoted original subject\\"" ' in result
    assert "unknown unknown" not in result
    assert format_changelog(result, repo, "compact") == result


def test_recorded_trailers_override_only_available_metadata_and_missing_main_is_explicit(
    history,
):
    repo, _ = history
    environment = dict(
        os.environ,
        GIT_AUTHOR_DATE="2026-01-04T10:00:00Z",
        GIT_COMMITTER_DATE="2026-01-04T10:00:00Z",
    )
    subprocess.check_call(
        ["git", "-C", str(repo), "checkout", "--orphan", "review"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subject = "Preserve recorded metadata\n\nLeader: Recorded owner\nWorker: worker-7\nBranch: release/topic"
    subprocess.check_call(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-q", "-m", subject],
        env=environment,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    source = f"## [1]\n\n### Other\n- Preserve recorded metadata {link(commit)}\n"
    result = format_changelog(source, repo)
    assert f'"Recorded owner" worker-7 2026-01-04 release/topic {commit}' in result
    assert "main-inferred" not in result
    assert format_changelog(result, repo) == result
    subprocess.check_call(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "Unrecorded branch",
        ],
        env=environment,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    result = format_changelog(
        f"## [1]\n\n### Other\n- Unrecorded branch {link(commit)}\n", repo
    )
    assert "contributor-inferred 2026-01-04 branch-unrecorded" in result


@pytest.mark.parametrize(
    ("subject", "kind"),
    [
        ("Add a feature", "Features"),
        ("Implement owner tools", "Features"),
        ("Introduce a provider", "Features"),
        ("Enable offline work", "Features"),
        ("Add tests for a provider", "Tests"),
        ("Introduce documentation", "Documentation"),
        ("Add tooling for CI", "Build and tooling"),
        ("feat: Add tests", "Features"),
    ],
)
def test_type_inference_is_conservative_and_explicit_prefix_wins(subject, kind):
    assert entry_type(subject) == kind


def test_actual_release_footer_remains_after_all_groups_without_highlight_blank_growth(
    history,
):
    repo, commits = history
    highlights = "### Highlights\n\nA release summary.\n\n- First highlight\n- Second highlight\n\n"
    footer = "\n[1.0.0]: https://example.invalid/compare/previous...1.0.0\n[desktop-v1]: https://example.invalid/releases/desktop-v1\n"
    source = (
        "# Changelog\n\nRelease introduction.\n\n## [1.0.0] - 2026-01-04\n\n"
        + highlights
        + f"### Features\n- fix: Keep the first value {link(commits[0])}\n\n"
        + f"### Other\n- An ordinary subject {link(commits[1])}\n"
        + footer
    )
    result = format_changelog(source, repo)
    assert highlights in result
    assert result.endswith(footer)
    assert result.index("[1.0.0]:") > result.index(link(commits[0]))
    assert result.index("[1.0.0]:") > result.index(link(commits[1]))
    assert "### Other" not in result.splitlines()
    assert format_changelog(result, repo) == result
    assert format_changelog(format_changelog(result, repo), repo) == result


def test_interleaved_unindented_prose_is_refused_instead_of_silently_moved(history):
    repo, commits = history
    source = f"## [1]\n\n### Other\n- First {link(commits[0])}\nAn intervening note.\n- Second {link(commits[1])}\n"
    with pytest.raises(FormatError, match="refusing to relocate"):
        format_changelog(source, repo)
