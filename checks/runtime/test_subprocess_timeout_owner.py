"""ONE owner for every asynchronous process deadline and kill (req.3, ac_1).

Five modules used to own a private copy of "the child blew its deadline": the installer
step, the console build, the command provider, the workflow container verb and the
teardown effect each had their own ``asyncio.wait_for`` + ``proc.kill()``. Each copy
forgot a different part — the group signal, the SIGTERM-before-SIGKILL escalation, the
bounded reap, closing our end of the pipes — and a copy that forgets the group leaves the
grandchild holding the inherited stdout, which turns the deadline into the grandchild's
full runtime.

So the rail is a census, not a list of blessed modules: an AST walk over every file in
``runtime/gideon`` collects

* **deadline sites** — ``wait_for`` around a subprocess ``communicate()``/``wait()``, and
* **kill sites** — ``os.killpg`` or ``kill``/``terminate``/``send_signal`` on a process,

keyed by ``file::qualname::callee``. Every key must be inside the owner
(:mod:`gideon.core.cancellation`) or named in :data:`_EXEMPT` with a reason. A new
hand-rolled timeout reds this test naming its own file, so the next one has to be a
conscious decision instead of a forgotten branch.

A second, interprocedural census starts from every bound asynchronous spawn and derives
whether its function can reach a process deadline and whether that path can reach
``kill_timed_out``. This keeps helper-based timeout paths visible under the spawn site
that owns the child. The few deliberately different lifecycles carry a durable reason.

:func:`test_the_census_is_not_vacuous` proves both matchers actually see the shapes they
are meant to catch, and ignore the ones they are not — a census that matched nothing
would let every assertion here pass by inspecting an empty set.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import gideon

_SRC = Path(gideon.__file__).resolve().parent
_OWNER = "core/cancellation.py"

_SPAWN_CALLEES = {
    "create_subprocess_exec",
    "create_subprocess_shell",
    "create_subprocess_limited",
    "Popen",
    "exec",
}
_ASYNC_SPAWN_CALLEES = _SPAWN_CALLEES - {"Popen"}
_KILL_CALLEES = {"kill", "terminate", "send_signal"}
_PROCESS_NAMES = {"proc", "process", "_process", "child", "subproc", "popen"}

_EXEMPT: dict[str, str] = {
    "integrations/acp/transport.py::AcpProcess._wait_exit::process.wait": (
        "ACP agent-session retirement, not a command deadline: it signals the pgid "
        "recorded at spawn (not only when the child still leads a group) and then sweeps "
        "descendants that escaped it — strictly more than the generic owner does"
    ),
    "integrations/acp/transport.py::AcpProcess._signal::killpg": (
        "the same session retirement's signal step — see AcpProcess._wait_exit"
    ),
    "integrations/acp/transport.py::AcpProcess._signal::self._process.kill": (
        "its pid fallback when the recorded pgid is no longer ours to signal"
    ),
    "interfaces/dashboard/handlers/terminal.py::api_terminal_ws.handle_exit::"
    "sess.proc.wait": (
        "observes an ALREADY-ending PTY session to report its exit code; the session's "
        "retirement belongs to _close_session, which calls terminate_and_reap"
    ),
    "engine/session.py::_ProcessSnapshot.terminate_survivors::killpg": (
        "pid-only reaper for a provider that outlived the handle we held for it — there "
        "is no Process object left to route"
    ),
    "engine/session_pid.py::_ProviderProcess._terminate_group::killpg": (
        "same: a recorded pgid for a leaked provider, reached without a Process handle"
    ),
    "engine/session_pid.py::_sync_kill_provider::process.terminate": (
        "the synchronous entry point of that same pid-only reaper"
    ),
    "engine/subagent.py::DelegationSupervisor._sigkill_session::killpg": (
        "pid-only reaper for a delegated session, with an ownership check on the pid"
    ),
    "extensions/apps/backend_runtime.py::BackendSupervisor.stop::proc.terminate": (
        "synchronous subprocess.Popen service supervisor — no asyncio deadline to own"
    ),
    "extensions/apps/backend_runtime.py::BackendSupervisor.stop::proc.kill": (
        "the escalation half of that synchronous supervisor"
    ),
    "extensions/apps/worker_runtime.py::WorkerSupervisor._terminate::proc.terminate": (
        "synchronous subprocess.Popen worker supervisor — no asyncio deadline to own"
    ),
    "extensions/apps/worker_runtime.py::WorkerSupervisor._terminate::proc.kill": (
        "the escalation half of that synchronous supervisor"
    ),
    "integrations/local_models/sidecar.py::SidecarRunner.stop::proc.terminate": (
        "synchronous subprocess.Popen sidecar lifecycle — closes its own pipes inline"
    ),
    "integrations/local_models/sidecar.py::SidecarRunner.stop::proc.kill": (
        "the escalation half of that synchronous lifecycle"
    ),
    "integrations/local_models/sidecar.py::SidecarRunner._died::proc.kill": (
        "the same synchronous lifecycle's crash path"
    ),
    "interfaces/cli/run.py::_shutdown_transient::proc.terminate": (
        "synchronous subprocess.Popen shutdown of the CLI's transient gateway"
    ),
    "interfaces/cli/run.py::_shutdown_transient::proc.kill": (
        "the escalation half of that synchronous shutdown"
    ),
}

_OWNED_BY_THE_OWNER = {
    "deadline": "core/cancellation.py::_ChildRetirement.attempt::self.process.wait",
    "kill": "core/cancellation.py::_signal_child::killpg",
}

_SPAWN_TIMEOUT_EXEMPT: dict[str, str] = {}


def _dotted(node: ast.AST) -> str:
    """``a.b.c`` for an attribute/name chain; ``()`` stands in for a call in the middle."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    elif isinstance(cur, ast.Call):
        parts.append("()")
    return ".".join(reversed(parts))


def _qualifier(tree: ast.AST):
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

    def qualname(node: ast.AST) -> str:
        names: list[str] = []
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(cur.name)
            cur = parents.get(cur)
        return ".".join(reversed(names)) or "<module>"

    return qualname


def _spawn_bound(tree: ast.AST) -> set[str]:
    """Names bound from a process spawn — so ``x.wait()`` can be told from ``event.wait()``."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if isinstance(value, ast.Await):
            value = value.value
        if not isinstance(value, ast.Call):
            continue
        fn = value.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in _SPAWN_CALLEES:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        bound.update(_dotted(target) for target in targets)
    return bound


def _is_process(receiver: str, spawned: set[str]) -> bool:
    return receiver in spawned or receiver.rsplit(".", 1)[-1] in _PROCESS_NAMES


def census(source: str, label: str = "x.py") -> dict[str, set[str]]:
    """``{"deadline": {key, …}, "kill": {key, …}}`` for one module's source."""
    tree = ast.parse(source)
    qualname = _qualifier(tree)
    spawned = _spawn_bound(tree)
    found: dict[str, set[str]] = {"deadline": set(), "kill": set()}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name == "wait_for" and node.args:
            inner = node.args[0]
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                receiver, attr = _dotted(inner.func.value), inner.func.attr
                if attr == "communicate" or (
                    attr == "wait" and _is_process(receiver, spawned)
                ):
                    found["deadline"].add(
                        f"{label}::{qualname(node)}::{receiver}.{attr}"
                    )
        elif name == "killpg":
            found["kill"].add(f"{label}::{qualname(node)}::killpg")
        elif name in _KILL_CALLEES and isinstance(fn, ast.Attribute):
            receiver = _dotted(fn.value)
            if _is_process(receiver, spawned):
                found["kill"].add(f"{label}::{qualname(node)}::{receiver}.{name}")
    return found


@lru_cache(maxsize=1)
def _runtime_census_cached() -> tuple[tuple[str, frozenset[str]], ...]:
    return tuple((kind, frozenset(keys)) for kind, keys in _walk_runtime().items())


def _runtime_census() -> dict[str, set[str]]:
    return {kind: set(keys) for kind, keys in _runtime_census_cached()}


def _walk_runtime() -> dict[str, set[str]]:
    total: dict[str, set[str]] = {"deadline": set(), "kill": set()}
    for path in sorted(_SRC.rglob("*.py")):
        label = str(path.relative_to(_SRC))
        for kind, keys in census(path.read_text(), label).items():
            total[kind] |= keys
    return total


@dataclass(frozen=True)
class _Function:
    key: str
    module: str
    qualname: str
    node: ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class _SpawnSite:
    key: str
    function: str


def _local_nodes(root: ast.AST):
    """Yield one function's nodes without borrowing calls from nested definitions."""
    pending = list(ast.iter_child_nodes(root))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))


def _defined_functions(
    sources: dict[str, str],
) -> tuple[dict[str, ast.AST], dict[str, _Function]]:
    trees: dict[str, ast.AST] = {}
    functions: dict[str, _Function] = {}
    for module, source in sources.items():
        tree = ast.parse(source)
        trees[module] = tree
        qualifier = _qualifier(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parent = qualifier(node)
            qualname = f"{parent}.{node.name}" if parent != "<module>" else node.name
            key = f"{module}::{qualname}"
            functions[key] = _Function(key, module, qualname, node)
    return trees, functions


def _call_name(node: ast.Call) -> str:
    return _dotted(node.func)


def _class_name(function: _Function) -> str:
    parts = function.qualname.rsplit(".", 1)
    return parts[0] if len(parts) == 2 else ""


def _constructor_aliases(function: _Function, classes: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in _local_nodes(function.node):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        constructor = _call_name(value).rsplit(".", 1)[-1]
        matches = {name for name in classes if name.rsplit(".", 1)[-1] == constructor}
        if len(matches) != 1:
            continue
        matched_class = next(iter(matches))
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = matched_class
    return aliases


def _resolved_calls(functions: dict[str, _Function]) -> dict[str, set[str]]:
    by_leaf: dict[str, set[str]] = {}
    by_suffix: dict[str, set[str]] = {}
    classes = {
        function.qualname.rsplit(".", 1)[0]
        for function in functions.values()
        if "." in function.qualname
    }
    for key, function in functions.items():
        leaf = function.qualname.rsplit(".", 1)[-1]
        by_leaf.setdefault(leaf, set()).add(key)
        by_suffix.setdefault(function.qualname, set()).add(key)

    edges = {key: set() for key in functions}
    for key, function in functions.items():
        aliases = _constructor_aliases(function, classes)
        owner_class = _class_name(function)
        for node in _local_nodes(function.node):
            if not isinstance(node, ast.Call):
                continue
            dotted = _call_name(node)
            if not dotted:
                continue
            parts = dotted.split(".")
            candidates: set[str] = set()
            if len(parts) == 1:
                candidates = by_leaf.get(parts[0], set())
            elif parts[0] in {"self", "cls"} and owner_class:
                candidates = by_suffix.get(f"{owner_class}.{parts[-1]}", set())
            elif parts[0] in aliases:
                candidates = by_suffix.get(f"{aliases[parts[0]]}.{parts[-1]}", set())
            else:
                suffix = ".".join(parts[-2:])
                candidates = by_suffix.get(suffix, set())
                if not candidates and len(by_leaf.get(parts[-1], set())) == 1:
                    candidates = by_leaf[parts[-1]]
            edges[key].update(candidates)
    return edges


def _callers_reaching(
    functions: dict[str, _Function], edges: dict[str, set[str]], seeds: set[str]
) -> set[str]:
    reached = set(seeds)
    changed = True
    while changed:
        changed = False
        for caller in functions:
            if caller not in reached and edges[caller] & reached:
                reached.add(caller)
                changed = True
    return reached


def _references_process(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in _PROCESS_NAMES:
            return True
        if isinstance(child, ast.Attribute):
            parts = set(_dotted(child).split("."))
            if parts & _PROCESS_NAMES:
                return True
    return False


def _has_direct_process_deadline(function: _Function) -> bool:
    for node in _local_nodes(function.node):
        if isinstance(node, ast.Call):
            name = _call_name(node).rsplit(".", 1)[-1]
            if name == "wait_for" and node.args and _references_process(node.args[0]):
                return True
        if isinstance(node, ast.AsyncWith):
            timed = any(
                isinstance(item.context_expr, ast.Call)
                and _call_name(item.context_expr).rsplit(".", 1)[-1]
                in {"timeout", "timeout_at"}
                for item in node.items
            )
            if timed and any(_references_process(statement) for statement in node.body):
                return True
    return False


def _spawn_sites(
    trees: dict[str, ast.AST], functions: dict[str, _Function]
) -> set[_SpawnSite]:
    function_keys = {
        (item.module, item.qualname): item.key for item in functions.values()
    }
    sites: set[_SpawnSite] = set()
    for module, tree in trees.items():
        qualifier = _qualifier(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if isinstance(value, ast.Await):
                value = value.value
            if not isinstance(value, ast.Call):
                continue
            if _call_name(value).rsplit(".", 1)[-1] not in _ASYNC_SPAWN_CALLEES:
                continue
            qualname = qualifier(node)
            function = function_keys.get((module, qualname))
            if function is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                binding = _dotted(target)
                if binding:
                    sites.add(_SpawnSite(f"{module}::{qualname}::{binding}", function))
    return sites


def _derived_spawn_census(sources: dict[str, str]) -> dict[str, tuple[bool, bool]]:
    """Map each async spawn to ``(has process deadline, reaches kill_timed_out)``."""
    trees, functions = _defined_functions(sources)
    edges = _resolved_calls(functions)
    deadlines = _callers_reaching(
        functions,
        edges,
        {key for key, item in functions.items() if _has_direct_process_deadline(item)},
    )
    kill_owners = {f"{_OWNER}::kill_timed_out"} & set(functions)
    owned = _callers_reaching(functions, edges, kill_owners)
    return {
        site.key: (
            site.function in deadlines or site.function in owned,
            site.function in owned,
        )
        for site in _spawn_sites(trees, functions)
    }


@lru_cache(maxsize=1)
def _runtime_spawn_census() -> dict[str, tuple[bool, bool]]:
    return _derived_spawn_census(
        {
            str(path.relative_to(_SRC)): path.read_text()
            for path in sorted(_SRC.rglob("*.py"))
        }
    )


def test_every_async_timeout_spawn_reaches_kill_timed_out_or_records_exception():
    census = _runtime_spawn_census()
    unowned = {
        key
        for key, (has_deadline, owned) in census.items()
        if has_deadline and not owned
    }
    missing = unowned - set(_SPAWN_TIMEOUT_EXEMPT)
    stale = set(_SPAWN_TIMEOUT_EXEMPT) - unowned

    assert not missing, (
        f"async subprocess timeout paths at {sorted(missing)} cannot reach "
        "gideon.core.cancellation.kill_timed_out. Route the path through "
        "run_with_timeout / wait_with_timeout, or record why its lifecycle genuinely "
        "requires different retirement semantics in _SPAWN_TIMEOUT_EXEMPT."
    )
    assert not stale, (
        f"_SPAWN_TIMEOUT_EXEMPT still names {sorted(stale)}, but those spawn sites no "
        "longer have an unowned timeout path; remove the stale reasons."
    )
    assert all(reason.strip() for reason in _SPAWN_TIMEOUT_EXEMPT.values())
    assert any(
        has_deadline and owned for has_deadline, owned in census.values()
    ), "the derived census found no timeout path reaching kill_timed_out"


def test_the_spawn_census_derives_timeout_ownership_through_helpers():
    owner = "async def kill_timed_out(proc):\n" "    return None\n"
    callers = (
        "import asyncio\n"
        "from gideon.core.cancellation import kill_timed_out\n"
        "async def bounded(proc):\n"
        "    try:\n"
        "        await asyncio.wait_for(proc.wait(), timeout=1)\n"
        "    except asyncio.TimeoutError:\n"
        "        await kill_timed_out(proc)\n"
        "async def owned():\n"
        "    proc = await asyncio.create_subprocess_exec('owned')\n"
        "    await bounded(proc)\n"
        "async def leaky():\n"
        "    proc = await asyncio.create_subprocess_exec('leaky')\n"
        "    await asyncio.wait_for(proc.communicate(), timeout=1)\n"
        "async def unbounded():\n"
        "    proc = await asyncio.create_subprocess_exec('unbounded')\n"
        "    await proc.wait()\n"
    )

    assert _derived_spawn_census({_OWNER: owner, "sample.py": callers}) == {
        "sample.py::leaky::proc": (True, False),
        "sample.py::owned::proc": (True, True),
        "sample.py::unbounded::proc": (False, False),
    }


def test_every_async_process_deadline_belongs_to_the_owner():
    """No module may hand-roll a timeout around ``communicate()``/``wait()``."""
    sites = _runtime_census()["deadline"]
    outside = {
        key for key in sites if not key.startswith(_OWNER) and key not in _EXEMPT
    }

    assert not outside, (
        f"{sorted(outside)} time a child out without the owner. Route it through "
        "gideon.core.cancellation.run_with_timeout / wait_with_timeout — that is where "
        "the group signal, the SIGTERM→SIGKILL escalation, the bounded reap and the pipe "
        "close all live. A private copy re-earns the leak one forgotten branch at a time; "
        "if this site genuinely is not a command deadline, add it to _EXEMPT with a reason."
    )
    assert _OWNED_BY_THE_OWNER["deadline"] in sites, (
        "the owner's own bounded wait vanished from the census — either it was renamed "
        "or the matcher stopped seeing it, and this rail would then pass vacuously"
    )


def test_every_async_process_kill_belongs_to_the_owner():
    """No module may signal a process it holds a handle for outside the owner."""
    sites = _runtime_census()["kill"]
    outside = {
        key for key in sites if not key.startswith(_OWNER) and key not in _EXEMPT
    }

    assert not outside, (
        f"{sorted(outside)} kill a process outside gideon.core.cancellation. Route it "
        "through terminate_and_reap (an owned child) or run_with_timeout (a blown "
        "deadline); a bare kill signals one pid, leaves the reap unbounded and leaves our "
        "end of the pipes open, which is how a grandchild outlives the command."
    )
    assert _OWNED_BY_THE_OWNER["kill"] in sites, (
        "the owner's own group signal vanished from the census — the matcher or the "
        "owner moved, and this rail would then pass vacuously"
    )


def test_the_exempt_list_has_no_stale_entries():
    """A reason for a site that no longer exists is a lie the next reader will believe."""
    sites = _runtime_census()
    live = sites["deadline"] | sites["kill"]
    stale = set(_EXEMPT) - live

    assert not stale, (
        f"_EXEMPT still names {sorted(stale)}, which no longer times out or kills "
        "anything. Drop the entry — a stale exemption is an exemption nobody re-earned."
    )


def test_the_census_is_not_vacuous():
    """Both directions, for both matchers: the shapes they must see, and the ones they must not."""
    hand_rolled = (
        "import asyncio\n"
        "async def run_step(argv):\n"
        "    proc = await asyncio.create_subprocess_exec(*argv)\n"
        "    try:\n"
        "        out = await asyncio.wait_for(proc.communicate(), timeout=5)\n"
        "    except asyncio.TimeoutError:\n"
        "        proc.kill()\n"
        "        return None\n"
    )
    seen = census(hand_rolled, "leaky.py")
    assert seen["deadline"] == {"leaky.py::run_step::proc.communicate"}
    assert seen["kill"] == {"leaky.py::run_step::proc.kill"}

    owned = (
        "import asyncio\n"
        "from gideon.core.cancellation import run_with_timeout\n"
        "async def run_step(argv):\n"
        "    proc = await asyncio.create_subprocess_exec(*argv)\n"
        "    return await run_with_timeout(proc, 5)\n"
    )
    assert census(owned, "owned.py") == {"deadline": set(), "kill": set()}

    not_a_process = (
        "import asyncio\n"
        "async def wait_for_shutdown(shutdown_event, queue):\n"
        "    await asyncio.wait_for(shutdown_event.wait(), timeout=5)\n"
        "    await asyncio.wait_for(queue.get(), timeout=5)\n"
    )
    assert census(not_a_process, "events.py") == {"deadline": set(), "kill": set()}

    spawn_bound_wait = (
        "import asyncio\n"
        "async def build():\n"
        "    stage = await asyncio.create_subprocess_exec('npm', 'run', 'build')\n"
        "    await asyncio.wait_for(stage.wait(), timeout=5)\n"
    )
    assert census(spawn_bound_wait, "build.py")["deadline"] == {
        "build.py::build::stage.wait"
    }


_GROUP_LED = {
    "automation/workflows/provisioning.py": "_StepExecution.subprocess",
    "operations/frontend.py": "build_frontend_async",
    "integrations/action_providers/bash_provider.py": "BashActionProvider.execute",
    "automation/workflows/container_env.py": "_run_cli",
    "automation/workflows/effects.py": "_TeardownInvocation.run",
}


def _session_led_spawns(source: str) -> set[str]:
    """Qualnames whose spawn asks for ``start_new_session=True``."""
    tree = ast.parse(source)
    qualname = _qualifier(tree)
    led: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in _SPAWN_CALLEES:
            continue
        if any(kw.arg == "start_new_session" for kw in node.keywords):
            led.add(qualname(node))
    return led


def test_the_four_named_call_sites_let_their_child_lead_a_group():
    """The owner's group branch only fires for a child that LEADS a group.

    ac_2 names installer, build, provider and workflow commands: each runs an arbitrary
    shell that forks. Without ``start_new_session`` the owner falls back to a single-pid
    signal — correct, but it leaves exactly the grandchild ac_2 forbids. So the flag is
    part of the deliverable, not an implementation detail.
    """
    missing = {
        f"{module}::{qualname}"
        for module, qualname in _GROUP_LED.items()
        if qualname not in _session_led_spawns((_SRC / module).read_text())
    }

    assert not missing, (
        f"{sorted(missing)} spawn a forking command without start_new_session, so the "
        "child does not lead a group and the owner can only signal its pid — the "
        "grandchild survives the timeout holding the inherited pipe."
    )


def test_the_session_matcher_sees_the_flag_both_ways():
    """VACUITY for the rail above."""
    source = (
        "import asyncio\n"
        "async def led():\n"
        "    await asyncio.create_subprocess_exec('sh', start_new_session=True)\n"
        "async def leaf():\n"
        "    await asyncio.create_subprocess_exec('git')\n"
    )
    assert _session_led_spawns(source) == {"led"}
