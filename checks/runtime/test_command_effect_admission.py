"""Command effects and protected removals at actual admission boundaries."""
from pathlib import Path
from types import SimpleNamespace
import pytest

from gideon.engine.task_modes import read_call, task_mode_denies
from gideon.integrations.llm.events import AgentEvent, EVENT_PERMISSION_REQUEST
from gideon.security.approval_brief import compose_approval_brief
from gideon.security.protected_folders import protected_delete

@pytest.mark.parametrize('command,risk', [
    ('ls -la', 'safe'), ('git status', 'safe'), ('git branch', 'safe'),
    ('git branch new', 'caution'), ('git branch -D old', 'destructive'),
    ('sort input -o output', 'caution'), ('uniq input output', 'caution'),
    ('find . -delete', 'destructive'), ('find . -exec touch {} \\;', 'unchecked'),
    ('mystery --help', 'unchecked'), ('ls --unknown', 'unchecked'),
    ('cat input 2>/dev/null', 'safe'), ('cat input 2>&1', 'safe'),
    ('cat input > output', 'caution'), ('cat $(touch output)', 'unchecked'),
    ('ls && echo hello', 'safe'), ('ls | sort -o output', 'caution'),
    ('curl https://example.test/', 'unchecked'), ('git push', 'unchecked'),
])
def test_raw_reading_drives_risk_facets_and_task_admission(command, risk):
    reading = read_call('destructive', 'bash', '', {'command': command})
    assert reading.risk == risk
    event = AgentEvent(EVENT_PERMISSION_REQUEST, title='bash', tool_input={'command': command}, risk_level='destructive')
    brief = compose_approval_brief(event)
    assert brief['risk'] == risk
    assert brief['blastRadius']['readOnly'] == (risk == 'safe')
    assert bool(task_mode_denies('ask', 'bash', '', event.tool_input, declared='destructive')) == (risk != 'safe')

@pytest.mark.parametrize('title,kind', [('run_script', 'execute'), ('mcp/s/read_file', 'read'), ('workflow_delete_def', '')])
def test_declared_tool_command_argument_is_data(title, kind):
    assert read_call('caution', title, kind, {'command': 'ls'}).risk == 'caution'
    assert task_mode_denies('ask', title, kind, {'command': 'ls'}, declared='caution')

@pytest.mark.parametrize('command', ['rm -rf .', 'rm . -rf', 'rmdir .', 'unlink .', 'find . -delete', 'sudo rm -rf .', "sh -c 'rm -rf .'", "eval 'rm -rf .'", 'env -C . rm -rf .', 'printf . | xargs rm -rf', 'rm -rf $UNDEFINED'])
def test_removal_facts_protect_actual_working_folder(tmp_path, command):
    assert protected_delete(command, cwd=str(tmp_path))

@pytest.mark.parametrize('command', ['rm -rf ./build', 'rm -rf /tmp/ordinary-build', 'rm -rf ~/.cache/ordinary-build'])
def test_targeted_cleanup_is_not_protected(tmp_path, command):
    assert not protected_delete(command, cwd=str(tmp_path))

def test_symlink_entry_differs_from_following_folder(tmp_path):
    home = Path.home()
    link = tmp_path / 'home-link'
    link.symlink_to(home, target_is_directory=True)
    assert not protected_delete(f'rm -rf {link}', cwd=str(tmp_path))
    assert protected_delete(f'rm -rf {link}/', cwd=str(tmp_path))

def test_definition_read_claim_does_not_admit_tool():
    event = AgentEvent(EVENT_PERMISSION_REQUEST, title='remote_lookup', tool_input={}, risk_level='caution', tool_annotations={'readOnlyHint': True})
    brief = compose_approval_brief(event)
    assert brief['blastRadius']['saysReadOnly']
    assert not brief['blastRadius']['readOnly']
    assert task_mode_denies('ask', event.title, 'read', {}, declared='caution')

@pytest.mark.asyncio
async def test_background_default_auto_and_hook_cannot_answer_protected_removal(tmp_path):
    from gideon.integrations.llm_helpers import _resolve_permission, ToolApprovalPolicy
    class Provider:
        _cwd = tmp_path
        approved = False
        rejected = False
        async def approve_tool(self, _): self.approved = True
        async def reject_tool(self, _): self.rejected = True
    provider = Provider()
    event = AgentEvent(EVENT_PERMISSION_REQUEST, title='bash', tool_input={'command': 'rm -rf .'}, risk_level='destructive')
    assert not await _resolve_permission(provider, event, ToolApprovalPolicy.AUTO_APPROVE, None)
    assert provider.rejected and not provider.approved

@pytest.mark.asyncio
async def test_native_bash_execution_last_screen_refuses_without_call_bound_answer(tmp_path):
    from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
    provider = NativeBuiltinToolProvider(cwd=tmp_path)
    result = await provider.invoke('bash', {'command': 'rm -rf .'})
    assert not result.success and 'working folder' in result.error
    assert tmp_path.exists()

def test_unattended_action_and_app_hook_refuse_before_execution(tmp_path):
    from gideon.security.guardrails.denylist import check_action
    from gideon.extensions.apps.app_manager import _run_hook, AppLifecycleError
    decision = check_action('bash', {'command': 'rm -rf .', 'cwd': str(tmp_path)})
    assert decision.blocked and decision.matched == 'protected_delete'
    with pytest.raises(AppLifecycleError, match='working folder'):
        _run_hook('rm -rf .', cwd=tmp_path, timeout=1, env_name='install')
    assert tmp_path.exists()


@pytest.mark.asyncio
async def test_native_auto_policy_cannot_skip_protected_approval(tmp_path):
    from test_native_runtime import _ScriptedModel, _Tool, _defn
    from gideon.engine.agents.native.runtime import NativeAgentRuntime, _NEEDS_APPROVAL
    from gideon.integrations.tool_providers.base import RiskLevel
    provider = _Tool(name='bash', requires_approval=False)
    runtime = NativeAgentRuntime(definition=_defn(), model_provider=_ScriptedModel([]), tool_providers=[provider], cwd=tmp_path, session_key='dashboard:main')
    await runtime.start()
    runtime._approval_policy = 'yolo'
    runtime._tool_risk['bash'] = RiskLevel.DESTRUCTIVE
    event = AgentEvent(EVENT_PERMISSION_REQUEST, title='bash', tool_input={'command': 'rm -rf .'})
    observation = await runtime._guard_and_invoke(event, 'bash', event.tool_input, meta={})
    assert observation is _NEEDS_APPROVAL
    assert not provider.invoked
    runtime._unattended = True
    observation = await runtime._guard_and_invoke(event, 'bash', event.tool_input, meta={})
    assert 'unattended' in observation and not provider.invoked


def test_internal_annotations_are_declarations_external_hints_are_claims():
    from gideon.engine.agents.native.tools import _describe_mcp_tool
    definition = _describe_mcp_tool({'name': 'read_customer_data', 'annotations': {'readOnlyHint': True}}, 'gideon-core', trusted_definition=True)
    assert definition.risk_level.value == 'safe'
    assert definition.annotations['readOnlyHint'] is True
    external = _describe_mcp_tool({'name': 'read_customer_data', 'annotations': {'readOnlyHint': True}}, 'external')
    assert external.risk_level.value == 'caution'
