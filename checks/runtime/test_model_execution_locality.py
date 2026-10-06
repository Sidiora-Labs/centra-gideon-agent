"""Execution proof uses registered declarations and model host facts without I/O."""

import importlib.util
import sys
from pathlib import Path

import pytest

import gideon.sdk.model
from gideon.engine.routing.rates import rate_for
from gideon.integrations.llm.capabilities import ProviderCapability
from gideon.integrations.llm.registry import (
    ProviderEntry,
    ProviderRegistry,
    endpoint_on_this_machine,
    get_default_registry,
    served_on_this_machine,
    set_default_registry,
)


@pytest.fixture
def registry():
    old = get_default_registry()
    current = ProviderRegistry()
    set_default_registry(current)
    yield current
    set_default_registry(old)


def cap(name, hosts=False):
    return ProviderCapability(
        name,
        frozenset(),
        True,
        True,
        False,
        False,
        0,
        hosts_model=hosts,
        default_endpoint="http://localhost:11434",
    )


def never_build(**kwargs):
    raise AssertionError("classification must not build a provider")


def test_loopback_relay_not_free_and_in_process_declared(registry, tmp_path):
    registry.register_type(cap("relay"), never_build)
    entry = ProviderEntry(
        "relay", "relay", model="unknown", options={"base_url": "http://localhost:9999"}
    )
    registry.register_entry(entry)
    assert endpoint_on_this_machine(entry)
    assert not served_on_this_machine(entry, "unknown")
    assert rate_for("relay", "unknown", home=tmp_path) is None
    registry.register_type(cap("engine"), never_build, in_process=True)
    engine = ProviderEntry("engine", "engine", model="own")
    registry.register_entry(engine)
    assert served_on_this_machine(engine, "own")
    assert rate_for("engine", "own", home=tmp_path).source == "local"


def test_probe_error_and_remote_endpoint_fail_closed(registry):
    registry.register_type(
        cap("host", True), never_build, passes_on=lambda entry, model: 1 / 0
    )
    assert not served_on_this_machine(ProviderEntry("host", "host", model="a"), "a")
    registry.register_type(cap("remote", True), never_build)
    assert not served_on_this_machine(
        ProviderEntry(
            "remote", "remote", model="a", options={"endpoint": "https://example.com"}
        ),
        "a",
    )


def test_ollama_host_facts_shared_across_copies_and_replacement(registry, tmp_path):
    path = (
        Path(__file__).resolve().parents[2]
        / "runtime/gideon/extensions/apps/native/ollama-models/provider.py"
    )

    def load(name):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    first = load("gideon_ollama_locality_first")
    second = load("gideon_ollama_locality_second")
    try:
        first._said().clear()
        entry = ProviderEntry(
            "ollama",
            "ollama",
            model="normal",
            options={"endpoint": "http://localhost:11434"},
        )
        registry.register_entry(entry)
        assert served_on_this_machine(entry, "normal")
        assert not served_on_this_machine(entry, "gpt-oss:120b-cloud")
        assert not served_on_this_machine(entry, "glm:cloud")
        assert served_on_this_machine(entry, "cloudlike:latest")
        second._heard_list(
            entry.options["endpoint"],
            [{"name": "normal:latest", "remote_host": "https://cloud.example"}],
        )
        assert not served_on_this_machine(entry, "normal")
        assert rate_for("ollama", "normal", home=tmp_path) is None
        second._heard_list(entry.options["endpoint"], [{"name": "normal:latest"}])
        assert served_on_this_machine(entry, "normal")
        second._heard_one("http://localhost:11435", "normal", {"remote_host": "other"})
        assert served_on_this_machine(entry, "normal")
        assert first._with_served(["chat"], ["embedding"]) == ["embedding"]
        assert first._with_served([], ["completion"]) == []
    finally:
        first._said().clear()
        sys.modules.pop("gideon_ollama_locality_first", None)
        sys.modules.pop("gideon_ollama_locality_second", None)
