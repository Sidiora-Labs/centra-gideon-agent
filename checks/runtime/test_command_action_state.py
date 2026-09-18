"""Command actions using local processes, script files and persisted prompt state."""

import asyncio
import json
import os
import shlex
import sys

import pytest

from gideon.cognition.context import PromptAssembler
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.subagent import DelegationSupervisor
from gideon.integrations.action_providers import run_prompt_provider as prompts
from gideon.integrations.action_providers import services
from gideon.integrations.action_providers.base import ActionContext, provider_failure
from gideon.integrations.action_providers.bash_provider import BashActionProvider
from gideon.integrations.action_providers.command_lifecycle import CommandProcess
from gideon.integrations.action_providers.run_script_provider import (
    RunScriptActionProvider,
)
from gideon.integrations.prompt_providers import registry as prompt_registry
from gideon.integrations.prompt_providers.base import (
    PromptSnippet,
    PromptTemplate,
    PromptVariable,
)
from gideon.integrations.prompt_providers.native_provider import NativePromptProvider
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security import trust_mode
from gideon.security.guardrails.project_trust import (
    project_decision,
    record_project_trust,
)


@pytest.fixture(autouse=True)
def action_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_PORT", "43187")
    (tmp_path / "config.json").write_text('{"providers": []}')
    monkeypatch.setattr(services, "_services", None)
    monkeypatch.setattr(prompt_registry, "_providers", {})
    monkeypatch.setattr(
        trust_mode._TRUST, "_on_disable", list(trust_mode._TRUST._on_disable)
    )
    return tmp_path


def python_command(body):
    return f"exec {shlex.quote(sys.executable)} -c {shlex.quote(body)}"


@pytest.mark.asyncio
async def test_bash_passes_json_and_safe_variables_without_shell_reinterpretation(
    action_home, monkeypatch
):
    marker = action_home / "should-not-exist"
    literal = f"$(touch {marker})"
    monkeypatch.setenv("COMMAND_TEST_PRIVATE_API_KEY", "must-not-inherit")
    payload = {
        "text": literal,
        "EVENT": "payload-event",
        "GIDEON_HOOK_EVENT": "forged",
        "PATH": "/invalid",
        "not-valid": "ignored",
    }
    command = python_command(
        "import json,os,sys; print(json.dumps({'input':json.load(sys.stdin),"
        "'text':os.getenv('text'),'event':os.getenv('EVENT'),"
        "'hook':os.getenv('GIDEON_HOOK_EVENT'),'path':os.getenv('PATH'),"
        "'invalid':os.getenv('not-valid'),'secret':os.getenv('COMMAND_TEST_PRIVATE_API_KEY')}))"
    )
    result = await BashActionProvider().execute(
        {"command": command}, ActionContext("Stop", "context", payload)
    )
    assert result.success, result.error or result.stderr
    observed = json.loads(result.stdout)
    assert observed["input"] == payload
    assert observed["text"] == literal and not marker.exists()
    assert observed["event"] == "payload-event" and observed["hook"] == "Stop"
    assert observed["path"] != "/invalid"
    assert observed["invalid"] is None and observed["secret"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [0, 2, 7])
async def test_bash_decodes_output_and_retains_exit_blocking_contract(code):
    command = python_command(
        f"import os; os.write(1,b'  out\\xff\\n'); os.write(2,b' err\\n'); raise SystemExit({code})"
    )
    result = await BashActionProvider().execute(
        {"command": command, "timeout": "invalid"}, ActionContext("PreToolUse")
    )
    assert result.success is (code == 0)
    assert result.blocked is (code == 2)
    assert result.exit_code == code
    assert result.stdout == "out\ufffd" and result.stderr == "err"
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_bash_timeout_reaps_owned_process(action_home):
    """The timed-out action's whole tree goes away — POLLED, like its cancellation sibling.

    The pid in the file is a GRANDchild of the action's own process: the owner
    (``gideon.core.cancellation``) signals the group synchronously, but the descendant's
    reaping belongs to init, so for a few milliseconds it is a zombie and ``os.kill(pid, 0)``
    still succeeds. Waiting that out used to be free because the old teardown ended with an
    UNBOUNDED ``communicate()`` — which is precisely the "deadline that waits for the
    grandchild" req.3 removed. Polling asserts the same fact without re-introducing it.
    """
    pidfile = action_home / "timed-process.pid"
    command = python_command(
        f"import os,time,pathlib; pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    result = await BashActionProvider().execute(
        {"command": command, "timeout": 1}, ActionContext("Stop"), timeout=20
    )
    assert not result.success and result.error == "Timed out after 1s"
    assert result.duration_ms >= 900
    assert pidfile.exists()
    pid = int(pidfile.read_text())
    async with asyncio.timeout(5):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_bash_cancellation_reaps_owned_process(action_home):
    pidfile = action_home / "cancelled-process.pid"
    command = python_command(
        f"import os,time,pathlib; pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    pending = asyncio.create_task(
        BashActionProvider().execute({"command": command}, ActionContext("Stop"))
    )
    try:
        async with asyncio.timeout(5):
            while not pidfile.exists():
                if pending.done():
                    pytest.fail(
                        f"child exited before writing its pid: {pending.result()}"
                    )
                await asyncio.sleep(0.01)
        pid = int(pidfile.read_text())
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    async with asyncio.timeout(5):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_owned_process_disposes_wrapper_file_after_timeout(action_home):
    wrapper = action_home / "sandbox-wrapper"
    wrapper.write_text("private launch data")
    owner = CommandProcess(str(wrapper))
    owner.process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        with pytest.raises(asyncio.TimeoutError):
            await owner.capture(b"", 0.05)
    finally:
        await owner.close()
    assert owner.completed and owner.process.returncode is not None
    assert not wrapper.exists()
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "success", "outcome", "message"),
    [
        ("return ctx.message", True, "", "schedule message"),
        ("raise Report('reported')", True, "", "reported"),
        ("raise Done('finished')", True, "done", "finished"),
        ("raise Skip()", True, "skip", ""),
        ("raise ValueError('script failed')", False, "", "script failed"),
    ],
)
async def test_scripts_execute_real_files_and_translate_outcomes(
    action_home, body, success, outcome, message
):
    scripts = action_home / "crons"
    scripts.mkdir()
    path = scripts / "routine.py"
    path.write_text(f"def run(ctx):\n    {body}\n")
    result = await RunScriptActionProvider().execute(
        {"script": f"{path}:run"}, ActionContext("Cron", "schedule message")
    )
    assert result.success is success, result.error
    assert result.outcome == outcome
    if success:
        assert result.stdout == message
    else:
        assert message in result.error


@pytest.mark.asyncio
async def test_script_timeout_is_enforced_by_actual_sandbox_runner(action_home):
    scripts = action_home / "crons"
    scripts.mkdir()
    path = scripts / "slow.py"
    path.write_text("import time\ndef run(ctx):\n    time.sleep(30)\n")
    result = await RunScriptActionProvider().execute(
        {"script": f"{path}:run", "timeout": 1}, ActionContext("Cron"), timeout=20
    )
    assert not result.success and "script timed out after 1s" in result.error


@pytest.mark.asyncio
async def test_script_outside_cron_root_is_refused_without_executing(action_home):
    marker = action_home / "must-not-run"
    path = action_home / "outside.py"
    path.write_text(
        f"from pathlib import Path\ndef run(ctx):\n    Path({str(marker)!r}).touch()\n"
    )
    result = await RunScriptActionProvider().execute(
        {"script": f"{path}:run"}, ActionContext("Cron")
    )
    assert not result.success and result.error
    assert not marker.exists()


def test_saved_prompt_reads_real_snippet_and_variable_files(action_home):
    source = NativePromptProvider()
    prompt_registry.register_prompt_provider(source)
    source.create_snippet(
        PromptSnippet(
            name="opening",
            content="Hello {{name}}",
            variables=[PromptVariable("name", required=True)],
        )
    )
    source.create_prompt(
        PromptTemplate(
            name="greeting",
            content="{{> opening}}!",
            variables=[PromptVariable("name", required=True)],
        )
    )
    assert (
        prompts.render_saved_prompt("greeting", {"name": "Gideon"}) == "Hello Gideon!"
    )
    with pytest.raises(ValueError):
        prompts.render_saved_prompt("greeting", {})
    with pytest.raises(LookupError, match="no saved prompt"):
        prompts.render_saved_prompt("missing", {})
    assert list((action_home / "prompts").glob("*.yaml"))


def test_loop_md_falls_back_from_blank_project_and_bounds_fresh_content(action_home):
    project = action_home / "project"
    project.mkdir()
    project_file = project / "loop.md"
    project_file.write_text("  \n")
    (action_home / "loop.md").write_text("user {{literal}}")
    assert prompts.resolve_loop_md(str(project)) == ("user {{literal}}", "user")
    project_file.write_text("x" * 16001)
    content, location = prompts.resolve_loop_md(str(project))
    assert content == "x" * 16000 + "\n…[loop.md truncated]"
    assert location == f"project:{project}"
    project_file.write_text("updated")
    assert prompts.resolve_loop_md(str(project))[0] == "updated"


def test_prompt_launch_preserves_session_and_project_trust_state(action_home):
    project = action_home / "project"
    project.mkdir()
    context = ActionContext("Cron", payload={"session_key": "parent-session"})
    config = {
        "agent": " reviewer ",
        "model": " model-name ",
        "max_turns": "invalid",
        "capability": " MUTATING ",
        "dry_run": True,
    }
    first = prompts._PromptLaunch.prepare(config, context, "task", str(project))
    assert first.arguments["capability_class"] == "research"
    assert project_decision(str(project)) == "preview"
    assert first.arguments["parent_session_key"] == "parent-session"
    assert (
        first.arguments["approval_mode"] == "auto"
        and first.arguments["silent"] is False
    )
    assert (
        first.arguments["agent"] == "reviewer"
        and first.arguments["model"] == "model-name"
    )
    assert first.arguments["dry_run"] is True and first.arguments["max_turns"] == 0
    record_project_trust(str(project), trusted=True)
    config.update(session=" pinned ", max_turns="12")
    trusted = prompts._PromptLaunch.prepare(config, context, "task", str(project))
    assert trusted.arguments["capability_class"] == "mutating"
    assert (
        trusted.arguments["parent_session_key"] == "pinned"
        and trusted.arguments["max_turns"] == 12
    )


def live_prompt_services():
    directory = ConversationDirectory(AppConfig.load())
    state = ConsoleState(sessions=directory, start_time=0)
    live = services.ActionServices(
        state, asyncio.create_task, DelegationSupervisor(directory, PromptAssembler())
    )
    services.set_action_services(live)
    return live


@pytest.mark.asyncio
async def test_prompt_cancellation_before_dispatch_starts_no_run(action_home):
    (action_home / "loop.md").write_text("Do local work")
    live = live_prompt_services()
    before = set(asyncio.all_tasks())
    result = await prompts.RunPromptActionProvider().execute({}, ActionContext("Cron"))
    created = set(asyncio.all_tasks()) - before
    assert result.success and result.outcome == "launched" and len(created) == 1
    task = created.pop()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert live.subagents._agents == {} and live.subagents._tasks == {}


@pytest.mark.asyncio
async def test_prompt_real_supervisor_refusal_settles_background_work(action_home):
    (action_home / "loop.md").write_text("Do local work")
    live = live_prompt_services()
    before = set(asyncio.all_tasks())
    result = await prompts.RunPromptActionProvider().execute(
        {"agent": "not-a-configured-agent"}, ActionContext("Cron")
    )
    assert result.success and result.outcome == "launched"
    await asyncio.gather(*(set(asyncio.all_tasks()) - before))
    assert live.subagents._agents == {} and live.subagents._tasks == {}


@pytest.mark.asyncio
async def test_prompt_closed_scheduler_propagates_failure_without_coroutine_leak(
    action_home,
):
    (action_home / "loop.md").write_text("Do local work")
    live = live_prompt_services()
    closed = asyncio.new_event_loop()
    closed.close()
    live.spawn_background = closed.create_task
    with pytest.raises(RuntimeError, match="closed"):
        await prompts.RunPromptActionProvider().execute({}, ActionContext("Cron"))
    assert live.subagents._agents == {}


@pytest.mark.asyncio
async def test_provider_default_reversal_and_machine_readable_failure():
    provider = RunScriptActionProvider()
    refusal = await provider.reverse("unknown:handle")
    assert (
        not refusal.success
        and refusal.error == "run-script cannot undo its own actions"
    )
    assert provider.reversal_kinds == () and not provider.supports_dry_run
    failure = provider_failure("run-script", ValueError("bad field")).to_dict()
    assert failure["code"] == "ERR_ACTION_PROVIDER_FAILED"
    assert (
        failure["what"] == "action provider 'run-script' failed: ValueError: bad field"
    )
    assert failure["why"] and failure["fix"] and failure["suggestions"] == []
