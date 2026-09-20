import pytest

from gideon.cognition.knowledge.pipeline.nodes.text_nodes import (
    DocumentReadNode,
    PassthroughNode,
)
from gideon.cognition.knowledge.pipeline.registry import get_node, register_node


@pytest.fixture
def _isolate_trigger_store():
    pass


def test_node_registry_is_isolated_per_pytest_worker(monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "registry-regression-gw0")
    passthrough = PassthroughNode()
    register_node(passthrough)

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "registry-regression-gw1")
    assert get_node(passthrough.node_type, passthrough.backend) is None
    document = DocumentReadNode()
    register_node(document)

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "registry-regression-gw0")
    assert get_node(passthrough.node_type, passthrough.backend) is passthrough
    assert get_node(document.node_type, document.backend) is None

    monkeypatch.delenv("PYTEST_XDIST_WORKER")
    assert get_node(passthrough.node_type, passthrough.backend) is None
    assert get_node(document.node_type, document.backend) is None
