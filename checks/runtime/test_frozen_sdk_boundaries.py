"""Frozen package children refuse before spawning; declared SDK streams work."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from gideon.sdk.features import CLOSING_STREAMS, CUT_OFF_ANSWERS, core_has
from gideon.sdk.model import closing_stream, until_terminal
from gideon.extensions.apps.manifest import AppManifest
from gideon.security.guardrails.failure import AnswerCutOff

ROOT=Path(__file__).resolve().parents[2]


def test_actual_frozen_child_process_refuses_all_pip_routes(tmp_path):
    code='''import sys
sys.frozen=True
from gideon.operations._installer import NoInstallerError, prefix_install_argv, env_install_argv
from gideon.core.python_children import require, NeedsInterpreter
for call in (lambda:prefix_install_argv(["example"]),lambda:env_install_argv("/missing",["example"])):
    try: call()
    except NoInstallerError as exc: assert "desktop app can't install Python packages" in str(exc)
    else: raise AssertionError("frozen installer accepted")
try: require("start a Python server")
except NeedsInterpreter as exc: print(exc)
else: raise AssertionError("frozen Python accepted")
'''
    result=subprocess.run([sys.executable,'-c',code],env=dict(os.environ,HOME=str(tmp_path),GIDEON_HOME=str(tmp_path/'gideon'),PYTHONPATH=str(ROOT/'runtime')),capture_output=True,text=True,check=True)
    assert 'gideon-agent-harness' in result.stdout
    assert not (tmp_path/'gideon').exists()


@pytest.mark.asyncio
async def test_advertised_closing_stream_closes_on_early_exit():
    closed=[]
    async def events():
        try:
            yield 1
            yield 2
        finally:
            closed.append(True)
    assert core_has(CLOSING_STREAMS)
    async with closing_stream(events()) as stream:
        async for event in stream:
            assert event==1
            break
    assert closed==[True]


@pytest.mark.asyncio
async def test_advertised_cutoff_contract_requires_real_terminal():
    async def events():
        yield 'partial'
    assert core_has(CUT_OFF_ANSWERS)
    with pytest.raises(AnswerCutOff):
        async for _ in until_terminal(events(),ends=lambda frame:frame=='done',adapter='probe',missing='done'):
            pass


def test_manifest_admits_only_implemented_sdk_features():
    supported=AppManifest(name='stream-probe',version='1.0.0',requiresCoreFeatures=[CLOSING_STREAMS,CUT_OFF_ANSWERS])
    assert supported.core_compatibility().admits
    unavailable=AppManifest(name='stream-probe',version='1.0.0',requiresCoreFeatures=['unimplemented-feature'])
    assert not unavailable.core_compatibility().admits
    assert 'unimplemented-feature' in unavailable.core_compatibility().reason
