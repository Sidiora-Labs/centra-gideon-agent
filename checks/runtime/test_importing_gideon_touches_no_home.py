from __future__ import annotations

import json
import os
import subprocess
import sys


def test_imports_resolve_no_home_or_create_files(tmp_path):
    home = tmp_path / "os-home"
    active = tmp_path / "gideon-home"
    home.mkdir()
    active.mkdir()
    script = r'''
import importlib, json, os, pkgutil, shutil, sys, traceback, types
from pathlib import PurePath
ROOTS = tuple(os.path.realpath(p) for p in sys.argv[1:3])
events = []
state = {"busy": False, "module": "<gideon import>", "phase": "import"}
PATH_ARGS = {
    "open": (0,), "sqlite3.connect": (0,), "os.mkdir": (0,), "os.rmdir": (0,),
    "os.remove": (0,), "os.rename": (0, 1), "os.link": (0, 1), "os.symlink": (1,),
    "os.truncate": (0,), "os.utime": (0,), "os.chmod": (0,), "os.listdir": (0,),
    "os.scandir": (0,), "shutil.rmtree": (0,), "shutil.copytree": (0, 1),
    "shutil.copyfile": (0, 1),
}
def under(value):
    if isinstance(value, (bytes, os.PathLike)):
        try: value = os.fsdecode(os.fspath(value))
        except (TypeError, ValueError): return None
    if not isinstance(value, str) or not value: return None
    if value.startswith("file:"): value = value[5:].split("?", 1)[0]
    if not os.path.isabs(value): return None
    path = os.path.normpath(value)
    return next((root for root in ROOTS if path == root or path.startswith(root + os.sep)), None)
def hook(event, args):
    if state["busy"] or event not in PATH_ARGS: return
    state["busy"] = True
    try:
        for index in PATH_ARGS[event]:
            if index < len(args):
                    root = under(args[index])
                    if root is not None:
                        stack = [
                            f"{frame.filename}:{frame.lineno} {frame.name}"
                            for frame in traceback.extract_stack()[:-1]
                            if "/runtime/gideon/" in frame.filename
                        ]
                        events.append({"phase": state["phase"],
                                       "module": state["module"], "event": event,
                                       "path": os.fsdecode(args[index]),
                                       "stack": stack[-8:]})
    finally:
        state["busy"] = False
sys.addaudithook(hook)
import gideon
failed = {}
state["module"] = "pkgutil.walk_packages"
names = sorted({info.name for info in pkgutil.walk_packages(
    gideon.__path__, "gideon.", onerror=lambda name: failed.setdefault(name, "walk")
)})
done = 0
for name in names:
    if name.rpartition(".")[2] == "__main__": continue
    state["module"] = name
    try:
        importlib.import_module(name)
        done += 1
    except Exception as exc:
        failed[name] = f"{type(exc).__name__}: {exc}"[:200]

state["phase"] = "inspection"

# A resolved-but-unread home path is invisible to audit hooks, so also find paths frozen in
# imported module globals. Concrete paths are frozen immediately; lazy PathLike values must
# follow both HOME and GIDEON_HOME when those settings change.
frozen, proxies, seen = [], [], set()
SKIP = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType,
        types.MethodType, type(os.environ))
def scan(obj, where, depth):
    if depth > 5 or id(obj) in seen: return
    seen.add(id(obj))
    if isinstance(obj, str):
        if under(obj) is not None: frozen.append(f"{where} = {obj}")
        return
    if isinstance(obj, os.PathLike):
        value = os.fspath(obj)
        if under(value) is not None:
            if isinstance(obj, PurePath):
                frozen.append(f"{where} = {value}")
            else:
                proxies.append((obj, where, value))
        return
    if isinstance(obj, SKIP) or isinstance(obj, (int, float, bytes, bool)) or obj is None: return
    if isinstance(obj, dict):
        for key, value in list(obj.items())[:500]: scan(value, f"{where}[{key!r}]", depth + 1)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for i, value in enumerate(list(obj)[:500]): scan(value, f"{where}[{i}]", depth + 1)
    else:
        attrs = getattr(obj, "__dict__", None)
        if isinstance(attrs, dict):
            for key, value in list(attrs.items())[:200]: scan(value, f"{where}.{key}", depth + 1)
for module_name, module in sorted(sys.modules.items()):
    if module_name == "gideon" or module_name.startswith("gideon."):
            for key, value in list(vars(module).items()):
                if not key.startswith("__"): scan(value, f"{module_name}.{key}", 0)

original_home = os.environ.get("HOME")
original_gideon_home = os.environ.get("GIDEON_HOME")
proxy_homes = (
    (os.path.join(ROOTS[0], "os-home-proxy-a"),
     os.path.join(ROOTS[0], "gideon-home-proxy-a")),
    (os.path.join(ROOTS[0], "os-home-proxy-b"),
     os.path.join(ROOTS[0], "gideon-home-proxy-b")),
)
try:
    resolved = {}
    for home, gideon_home in proxy_homes:
        os.environ["HOME"] = home
        os.environ["GIDEON_HOME"] = gideon_home
        resolved[(home, gideon_home)] = [os.fspath(proxy) for proxy, _, _ in proxies]
    for index, (proxy, where, initial) in enumerate(proxies):
        first = resolved[proxy_homes[0]][index]
        second = resolved[proxy_homes[1]][index]
        if first == second:
            frozen.append(f"{where} = {initial}")
finally:
    if original_home is None: os.environ.pop("HOME", None)
    else: os.environ["HOME"] = original_home
    if original_gideon_home is None: os.environ.pop("GIDEON_HOME", None)
    else: os.environ["GIDEON_HOME"] = original_gideon_home
for _, gideon_home in proxy_homes:
    shutil.rmtree(gideon_home, ignore_errors=True)
left = sorted(os.path.relpath(os.path.join(d, n), ROOTS[0])
              for d, dirs, files in os.walk(ROOTS[0]) for n in dirs + files)
print(json.dumps({"walked": len(names), "done": done, "failed": failed,
                  "touches": [event for event in events if event["phase"] == "import"],
                  "inspection_events": [event for event in events if event["phase"] != "import"],
                  "frozen": frozen, "left": left}))
'''
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIDEON_", "PYTEST_", "COV_CORE_"))
    }
    env.update(
        HOME=str(home),
        GIDEON_HOME=str(active),
        PYTHONPATH=os.pathsep.join(
            [os.path.abspath("runtime"), env.get("PYTHONPATH", "")]
        ),
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(home), str(active)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["walked"] >= 1000, f"module walk found only {report['walked']} modules: {report['failed']}"
    assert report["done"] >= 1000, (
        f"only {report['done']} modules imported from {report['walked']} discovered; "
        f"failures: {report['failed']}"
    )
    assert not report["touches"], (
        "module imports touched HOME/GIDEON_HOME: "
        + json.dumps(report["touches"][:30], indent=2)
    )
    assert not report["frozen"], f"modules captured home paths at import: {report['frozen'][:30]}"
    assert report["left"] == ["gideon-home", "os-home"], (
        f"module imports left home files behind: {report['left']}"
    )
    assert list(home.iterdir()) == []
    assert list(active.iterdir()) == []
