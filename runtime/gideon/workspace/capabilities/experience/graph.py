"""Bounded authored story graphs with deterministic transitions."""

import re


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("invalid identifier")
    return value


def revision(value):
    if type(value) is not int or value < 1:
        raise ValueError("revision must be a positive integer")
    return value


def text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"text must contain 1 to {limit} characters")
    return value.strip()


def validate_graph(body):
    if not isinstance(body, dict) or set(body) - {"title", "start_node", "nodes", "transitions", "revision"}:
        raise ValueError("unsupported story fields")
    title, start = text(body.get("title"), 200), identifier(body.get("start_node"))
    nodes = body.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 200:
        raise ValueError("a story requires 1 to 200 nodes")
    result, transitions, ids = [], [], set()
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {"id", "text", "kind", "choices"}:
            raise ValueError("node requires id, text, kind and choices")
        key, prose = identifier(node["id"]), text(node["text"], 10000)
        if key in ids or node["kind"] not in ("scene", "ending"):
            raise ValueError("duplicate node or invalid kind")
        ids.add(key)
        choices = node["choices"]
        if not isinstance(choices, list) or len(choices) > 20:
            raise ValueError("choices must contain at most 20 items")
        if (node["kind"] == "ending") != (len(choices) == 0):
            raise ValueError("ending nodes have no choices; scenes require choices")
        normalized, choice_ids = [], set()
        for choice in choices:
            if not isinstance(choice, dict) or set(choice) != {"id", "label", "target"}:
                raise ValueError("choice requires id, label and target")
            cid, target = identifier(choice["id"]), identifier(choice["target"])
            if cid in choice_ids:
                raise ValueError("duplicate choice identifier")
            choice_ids.add(cid)
            normalized.append({"id": cid, "label": text(choice["label"], 200), "target": target})
            transitions.append({"source": key, "choice_id": cid, "target": target})
        result.append({"id": key, "text": prose, "kind": node["kind"], "choices": normalized})
    if start not in ids or any(t["target"] not in ids for t in transitions):
        raise ValueError("start node or choice target does not exist")
    reached, pending = set(), [start]
    while pending:
        key = pending.pop()
        if key not in reached:
            reached.add(key)
            pending.extend(t["target"] for t in transitions if t["source"] == key)
    endings = {n["id"] for n in result if n["kind"] == "ending"}
    while True:
        expanded = endings | {t["source"] for t in transitions if t["target"] in endings}
        if expanded == endings:
            break
        endings = expanded
    if reached != ids or endings != ids:
        raise ValueError("every node must be reachable and able to reach an ending")
    if "transitions" in body and body["transitions"] != transitions:
        raise ValueError("transitions must match node choices")
    return {"title": title, "start_node": start, "nodes": result, "transitions": transitions}
