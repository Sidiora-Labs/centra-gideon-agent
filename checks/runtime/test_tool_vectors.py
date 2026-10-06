"""Restart and identity behavior for persisted tool-description vectors."""

import json

from gideon.engine.agents.native import tool_vectors


def test_restart_reads_only_vectors_for_exact_model_and_text(tmp_path):
    model = "local:embed-a"
    first = "read_file: read a file"
    second = "write_file: write a file"
    vectors = {
        tool_vectors._digest(model, first): tool_vectors._pack([0.6, 0.8]),
        tool_vectors._digest(model, second): tool_vectors._pack([0.8, 0.6]),
    }
    path = tmp_path / tool_vectors.TOOL_VECTORS_FILE
    path.write_text(json.dumps({"version": 1, "vectors": vectors}), encoding="utf-8")

    restarted = tool_vectors.ToolVectors()
    loaded = restarted.vectors(path, model, (first, second))
    assert loaded == {
        first: [0.6000000238418579, 0.800000011920929],
        second: [0.800000011920929, 0.6000000238418579],
    }
    assert restarted.vectors(path, "local:embed-b", (first, second)) == {}
    assert restarted.vectors(path, model, ("read_file: read a document",)) == {}


def test_corrupt_cache_fails_open_as_empty(tmp_path):
    path = tmp_path / tool_vectors.TOOL_VECTORS_FILE
    path.write_text("{not json", encoding="utf-8")
    assert (
        tool_vectors.ToolVectors().vectors(path, "local:embed-a", ("tool: text",)) == {}
    )
