"""Tests for the harness static boundary scanner + diff-aware selection (Session 2).

The load-bearing test is `test_scanner_clean_on_current_tree`: the scanner must produce
ZERO error-level findings on the real repo, or it's noise. The rest prove each check
actually FIRES on a synthetic violation (a check that never fires is worthless) and that
touched-area → profile forcing works.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from checks.harness import scanner
from checks.harness.diff import _parse_added_lines, has_fix_shaped_commit, touches_specs
from checks.harness.selection import forced_profiles


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=False
    ).stdout
    return [root / ln.strip() for ln in out.splitlines() if ln.strip()]


def test_scanner_clean_on_current_tree() -> None:
    """No ERROR-level scanner finding on the real repo. WARNINGs are allowed (advisory)."""
    root = _repo_root()
    findings = scanner.scan(_tracked_files(root), root)
    errors = [f for f in findings if f.level == scanner.ERROR]
    assert (
        not errors
    ), "scanner has false-positive ERRORs on a clean tree:\n" + "\n".join(
        f.format(root) for f in errors
    )


def test_known_checks_matches_seed_rule_scanner_refs() -> None:
    """Every scanner check-id a shipped rule spec references actually exists."""
    from checks.harness.specs import load_specs

    referenced = {str(s.meta["scanner"]) for s in load_specs() if s.meta.get("scanner")}
    assert referenced, "seed rules should reference scanner checks"
    assert (
        referenced <= scanner.known_checks()
    ), f"rule specs reference unknown scanner checks: {referenced - scanner.known_checks()}"


def test_config_four_points_fires_on_missing_load_mapping(tmp_path: Path) -> None:
    root = tmp_path
    loader = root / "runtime" / "gideon" / "core" / "config" / "loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        textwrap.dedent("""
            from dataclasses import dataclass, field
            def _meta(label, help, **k): return {"label": label}
            @dataclass
            class WidgetConfig:
                mapped_field: bool = field(default=True, metadata=_meta("A", "a"))
                forgotten_field: bool = field(default=False, metadata=_meta("B", "b"))
            @dataclass
            class AppConfig:
                @classmethod
                def load(cls, data):
                    w = data.get("widget", {})
                    return cls(widget=WidgetConfig(mapped_field=bool(w.get("mapped_field", True))))
            """),
        encoding="utf-8",
    )
    findings = [
        f for f in scanner.scan([loader], root) if f.check == "config-four-points"
    ]
    names = {f.what for f in findings}
    assert any("forgotten_field" in n for n in names)
    assert not any("mapped_field" in n for n in names)


def test_hook_provider_parity_fires_on_unlisted_provider(tmp_path: Path) -> None:
    root = tmp_path
    val = root / "runtime" / "gideon" / "assurance" / "validation.py"
    val.parent.mkdir(parents=True)
    val.write_text(
        'ALLOWED_HOOK_PROVIDERS = frozenset({"bash", "webhook"})\n', encoding="utf-8"
    )
    ap = root / "runtime" / "gideon" / "integrations" / "action_providers"
    ap.mkdir(parents=True)
    (ap / "ghost_provider.py").write_text(
        textwrap.dedent("""
            class GhostActionProvider:
                @property
                def name(self) -> str:
                    return "ghost"
            """),
        encoding="utf-8",
    )
    findings = [
        f
        for f in scanner.scan([ap / "ghost_provider.py"], root)
        if f.check == "hook-provider-parity"
    ]
    assert any("ghost" in f.what for f in findings)


def test_sse_event_registered_fires_on_unregistered_event(tmp_path: Path) -> None:
    root = tmp_path
    fe = root / "apps/console" / "src" / "pages" / "loops" / "useRunStream.ts"
    fe.parent.mkdir(parents=True)
    fe.write_text(
        "export const RUN_LIFECYCLE = ['known_event'] as const\n", encoding="utf-8"
    )
    py = root / "runtime" / "gideon" / "automation" / "loop" / "kinds" / "x.py"
    py.parent.mkdir(parents=True)
    py.write_text(
        'def go(ctx, cid):\n    ctx.publish(cid, "unregistered_event", {})\n',
        encoding="utf-8",
    )
    findings = [
        f for f in scanner.scan([py], root) if f.check == "sse-event-registered"
    ]
    assert any("unregistered_event" in f.what for f in findings)


def test_sse_event_registered_ignores_registered_and_nonloop(tmp_path: Path) -> None:
    root = tmp_path
    fe = root / "apps/console" / "src" / "pages" / "loops" / "useRunStream.ts"
    fe.parent.mkdir(parents=True)
    fe.write_text("export const RUN_LIFECYCLE = ['known'] as const\n", encoding="utf-8")
    py = root / "runtime" / "gideon" / "automation" / "loop" / "kinds" / "x.py"
    py.parent.mkdir(parents=True)
    py.write_text(
        "def go(ctx, other):\n"
        '    ctx.publish(1, "known", {})\n'
        '    other.publish(1, "some_other_registry_event", {})\n',
        encoding="utf-8",
    )
    findings = [
        f for f in scanner.scan([py], root) if f.check == "sse-event-registered"
    ]
    assert findings == []


def test_app_sdk_boundary_fires_on_deep_import(tmp_path: Path) -> None:
    root = tmp_path
    appf = root / "apps" / "demo" / "provider.py"
    appf.parent.mkdir(parents=True)
    appf.write_text(
        "from gideon.automation.loop.worktree import thing\n", encoding="utf-8"
    )
    findings = [f for f in scanner.scan([appf], root) if f.check == "app-sdk-boundary"]
    assert any("gideon.automation.loop.worktree" in f.what for f in findings)


def test_app_sdk_boundary_allows_sdk_import(tmp_path: Path) -> None:
    root = tmp_path
    appf = root / "apps" / "demo" / "provider.py"
    appf.parent.mkdir(parents=True)
    appf.write_text("from gideon.sdk import net\n", encoding="utf-8")
    findings = [f for f in scanner.scan([appf], root) if f.check == "app-sdk-boundary"]
    assert findings == []


def _selfqa_module(root: Path, name: str, body: str) -> Path:
    """A synthetic module under the path the check scopes to, carrying `body`."""
    f = root / "runtime" / "gideon" / "assurance" / "selfqa" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(textwrap.dedent(body), encoding="utf-8")
    return f


def _watcher_findings(files: list[Path], root: Path) -> list[scanner.Finding]:
    return [
        f for f in scanner.scan(files, root) if f.check == "no-periodic-commit-watcher"
    ]


def test_no_periodic_commit_watcher_fires_on_a_returning_interval(
    tmp_path: Path,
) -> None:
    """🔴 The retirement, enforced: a clock put back beside the commit watch is an ERROR."""
    f = _selfqa_module(
        tmp_path,
        "relapse.py",
        """
        def reconcile(store, trigger):
            trigger.kind = "interval"
            trigger.spec = {"interval_minutes": 5}
            trigger.workflow = {"inline": {"provider": "selfqa-commit-watch"}}
            store.upsert(trigger)
        """,
    )
    findings = _watcher_findings([f], tmp_path)
    assert findings, "the retired periodic watcher came back unflagged"
    assert all(hit.level == scanner.ERROR for hit in findings)
    lines = f.read_text(encoding="utf-8").splitlines()
    assert any(
        "interval" in lines[hit.line - 1] for hit in findings
    ), f"the finding does not point at the cadence: {[hit.line for hit in findings]}"


def test_no_periodic_commit_watcher_allows_the_vcs_trigger(tmp_path: Path) -> None:
    """🪤 The vacuity floor. The compliant binding — the one the repo actually ships —
    must stay silent, or the check is just "mentions the commit watch"."""
    f = _selfqa_module(
        tmp_path,
        "compliant.py",
        """
        def reconcile(store, trigger, repo):
            trigger.kind = "file"
            trigger.spec = {"paths": vcs_patterns(repo), "dedup": "content"}
            trigger.workflow = {"inline": {"provider": "selfqa-commit-watch"}}
            store.upsert(trigger)
        """,
    )
    assert _watcher_findings([f], tmp_path) == []


def test_no_periodic_commit_watcher_ignores_a_schedule_elsewhere(
    tmp_path: Path,
) -> None:
    """A module that schedules something ELSE is not the commit watcher returning."""
    f = _selfqa_module(
        tmp_path,
        "unrelated.py",
        """
        def arm(store, trigger):
            trigger.kind = "interval"
            trigger.spec = {"interval_minutes": 30}
            store.upsert(trigger)
        """,
    )
    assert _watcher_findings([f], tmp_path) == []


def test_no_periodic_commit_watcher_reads_prose_as_prose(tmp_path: Path) -> None:
    """The retirement is NARRATED in these modules — a docstring about the removed cron
    script must not read as the cron script."""
    f = _selfqa_module(
        tmp_path,
        "narrated.py",
        "\n".join(
            [
                '"""The interim cron script (crons/selfqa_commit_watch.py) ran on an',
                'interval schedule; the vcs preset owns this now."""',
                "",
                'WATCH_TRIGGER_ID = "system:selfqa-commit-watch"',
                "",
            ]
        ),
    )
    assert _watcher_findings([f], tmp_path) == []


def test_no_periodic_commit_watcher_is_clean_on_the_shipped_watcher() -> None:
    """The real modules, not a synthetic one: the tree as it stands has no periodic watcher.

    `test_scanner_clean_on_current_tree` covers this in aggregate; this names the files so a
    relapse reds a test whose name says what broke.
    """
    root = _repo_root()
    watcher_files = [
        root / "runtime/gideon/assurance/selfqa/watch.py",
        root / "runtime/gideon/assurance/selfqa/install.py",
        root / "runtime/gideon/integrations/action_providers/selfqa_watch_provider.py",
    ]
    assert all(
        f.is_file() for f in watcher_files
    ), f"the watcher moved: {watcher_files}"
    findings = _watcher_findings(watcher_files, root)
    assert findings == [], "\n".join(f.format(root) for f in findings)


def test_chat_touch_forces_replay_and_web() -> None:
    forced = {
        f.profile
        for f in forced_profiles(["apps/console/src/pages/chat/coalesceReducers.ts"])
    }
    assert "replay" in forced
    assert "web" in forced


def test_config_loader_touch_forces_scan() -> None:
    forced = {
        f.profile for f in forced_profiles(["runtime/gideon/core/config/loader.py"])
    }
    assert "scan" in forced


def test_unrelated_touch_forces_nothing_sensitive() -> None:
    forced = {f.profile for f in forced_profiles(["README.md"])}
    assert "replay" not in forced and "scan" not in forced and "web" not in forced


def test_fix_shaped_detection() -> None:
    assert has_fix_shaped_commit(["fix(loop): stop double-count"])
    assert has_fix_shaped_commit(["bugfix: nasty regression"])
    assert not has_fix_shaped_commit(["feat(x): add thing", "docs: tidy"])


def test_touches_specs_detection() -> None:
    assert touches_specs(["checks/harness/specs/rules/new.md", "src/x.py"])
    assert not touches_specs(["src/x.py", "apps/console/y.ts"])


def test_parse_added_lines_reads_hunks() -> None:
    patch = textwrap.dedent("""\
        diff --git a/x.py b/x.py
        --- a/x.py
        +++ b/x.py
        @@ -1,0 +2,2 @@
        +added line one
        +added line two
        """)
    parsed = _parse_added_lines(patch)
    assert parsed == {"x.py": {2, 3}}
