# Runtime tests

Pytest collects runtime tests from `checks/runtime` by default. The suite exercises
configuration, gateway routes, provider contracts, authorization, apps, memory, and
other runtime consumers. File counts and a historical green run are not a guarantee of
current coverage or pass status.

## Commands

Use the prepared development environment described in
[CONTRIBUTING.md](../../CONTRIBUTING.md), then run from the repository root:

```bash
make test
python -m pytest checks/runtime/test_provider_registry.py -v
python -m pytest -k provider_lazy_imports -v
python -m pytest --lf -v
```

`make test` invokes pytest; it is not the whole lint, native, console, browser, or release
gate. See the actual Makefile and workflow commands for those scopes. The root pytest
configuration uses asyncio `auto` mode, not `strict`.

## Isolation and bytecode

Set an isolated `GIDEON_HOME` before importing code for tests that could touch persistent
state. Use temporary workspace and data paths separately: home isolation does not by
itself constrain the workspace. An explicitly supplied home is respected by fixtures,
so never point a destructive test at the real user home.

`conftest.py` activates `pycache_guard.py` before importing runtime code. Fresh bytecode
storage avoids stale `.pyc` reuse during rapid same-size source mutations. `python -B`
only disables writes; it is not a general stale-bytecode solution. The guard does not
restore a mutation left in source after an interrupted run.

## Meaningful consumer checks

Use temporary paths and configuration fixtures where appropriate. Distinguish a pure
unit seam, scripted protocol fixture, real gateway route, real native daemon, and a live
vendor integration in the result. Mocking an external process cannot establish its
actual launch, protocol, identity, or shutdown behavior. Choose the necessary consumer
and report unavailable prerequisites.

Assertions should cover both the intended behavior and the relevant refusal or benign
control. A transport response or accepted-work acknowledgement does not prove terminal
completion or an external effect.

## Manual scripts

`smoke_gateway.sh`, `smoke_sandbox.sh`, and `debug_sandbox.sh` are separate manual
utilities. Inspect their environment and targets before running: they are not invoked
by the ordinary `make test` target, and a running gateway or platform sandbox may be
required. Do not treat the script's presence as current live verification.
