"""Actual native session and local tool providers, with no inference calls."""
import time
from pathlib import Path
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory, _Session
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm.credentials import Credential
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.workspace.capabilities.identity.guarded_recipes import GuardedRecipes


async def fixture(home: Path, *, dry_run=False):
    workspace = home / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    model = OpenAIProvider(model='local-test-unused', credential=Credential('test', 'api_key', 'unused-local-fixture'), base_url='http://127.0.0.1:1/v1')
    native = NativeBuiltinToolProvider(cwd=workspace)
    identity = IdentityToolProvider(home)
    runtime = NativeAgentRuntime(definition=AgentRuntimeDefinition(name='fixture'), model_provider=model, tool_providers=[native, identity], cwd=workspace, session_key='dashboard:recipes', dry_run=dry_run)
    await runtime.start()
    directory = ConversationDirectory(AppConfig())
    directory._sessions['dashboard:recipes'] = _Session(runtime)
    state = ConsoleState(directory, time.time())
    service = GuardedRecipes(home, state)
    return service, runtime, state, workspace, model
