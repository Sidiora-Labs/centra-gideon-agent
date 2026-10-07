"""Explicit package-owned child commands for the frozen desktop runtime."""

from __future__ import annotations

import os
import runpy
import sys

CHILD_MODULES = (
    "gideon.engine._spawn_exec_shim",
    "gideon.integrations.computer_use.driver_host",
    "gideon.assurance.evals.child",
    "gideon.extensions.providers.availability_probe",
    "gideon._app_python_child",
    "gideon.security.namespace_child",
    "gideon.workspace.uploads.scan_child",
)


def restore_environment(environment=None):
    target = os.environ if environment is None else environment
    for key in list(target):
        if key.startswith("_PYI_"):
            del target[key]
    if sys.platform.startswith("linux"):
        original = target.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is not None:
            target["LD_LIBRARY_PATH"] = original
        elif target.get("LD_LIBRARY_PATH") == getattr(sys, "_MEIPASS", None):
            target.pop("LD_LIBRARY_PATH", None)
    return target


def child_module(argv):
    if len(argv) >= 3 and argv[1] == "-m" and argv[2] in CHILD_MODULES:
        return argv[2]
    return None


def run(module):
    if module not in CHILD_MODULES:
        raise ValueError("Undeclared frozen child module")
    del sys.argv[1:3]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    raise SystemExit(0)
