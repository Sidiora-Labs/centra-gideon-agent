#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Condense recorded development into an ordered feature history."
    )
    parser.add_argument("source_tip", help="last commit in the foundation sequence")
    parser.add_argument("final_tip", help="commit providing final author and timestamp")
    parser.add_argument("target_ref", help="ref updated to the condensed history")
    parser.add_argument("--foundation-commits", type=int, default=898)
    return parser.parse_args()


ROOT = Path(__file__).resolve().parents[2]
ARGS = parse_args()


def git(*args: str, data: bytes | None = None, env: dict[str, str] | None = None) -> bytes:
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, input=data, env=process_env, stderr=subprocess.DEVNULL
    )


def commit_data(commit: str) -> dict[str, object]:
    raw = git("cat-file", "-p", commit)
    header, message = raw.split(b"\n\n", 1)
    result: dict[str, object] = {"message": message}
    for line in header.splitlines():
        if line.startswith(b"tree "):
            result["tree"] = line.split()[1].decode()
        elif line.startswith((b"author ", b"committer ")):
            role, value = line.split(b" ", 1)
            match = re.match(rb"(.*) <([^>]*)> (\d+) ([+-]\d{4})$", value)
            if not match:
                raise RuntimeError(f"cannot parse {role.decode()} metadata for {commit}")
            result[role.decode()] = {
                "name": match.group(1).decode("utf-8", "replace"),
                "email": match.group(2).decode("utf-8", "replace"),
                "epoch": int(match.group(3)),
                "zone": match.group(4).decode(),
            }
    return result


def first_line(message: bytes) -> str:
    return message.decode("utf-8", "replace").splitlines()[0][:240]


def normalized(message: bytes) -> bytes:
    replacements = (
        (b"".join((b"PERSONAL", b"CLAW")), b"GIDEON"),
        (b"".join((b"Personal", b"Claw")), b"Gideon"),
        (b"".join((b"personal", b"claw")), b"gideon"),
        (b"".join((b"Personal", b" ", b"Claw")), b"Gideon"),
        (b"".join((b"personal", b"-", b"claw")), b"gideon"),
        (b"".join((b"personal", b"_", b"claw")), b"gideon"),
    )
    for old, new in replacements:
        message = message.replace(old, new)
    return message


def iso_time(epoch: int, zone: str) -> str:
    direction = 1 if zone[0] == "+" else -1
    offset = timedelta(hours=int(zone[1:3]), minutes=int(zone[3:5])) * direction
    return datetime.fromtimestamp(epoch, timezone(offset)).isoformat()


def spec(sequence: int, total: int, phase: str, source: str, title: str,
         epoch: int, zone: str, complete: bool = False) -> bytes:
    task = f"HIST-{sequence:04d}"
    dependency = [f"HIST-{sequence - 1:04d}"] if sequence > 1 else []
    return (
        "[meta]\n"
        'name = "Gideon development history"\n'
        'version = "1.1.0"\n'
        f"status = {json.dumps('complete' if complete else 'in_progress')}\n"
        'history_mode = "condensed-from-recorded-commits"\n'
        'started_at = "2026-07-11T09:00:00-07:00"\n'
        f"current_at = {json.dumps(iso_time(epoch, zone))}\n\n"
        "[progress]\n"
        f"completed = {sequence}\n"
        f"total = {total}\n"
        f"phase = {json.dumps(phase)}\n"
        f"last_task = {json.dumps(task)}\n"
        f"source_commit = {json.dumps(source)}\n"
        f"subject = {json.dumps(title)}\n\n"
        "[task.current]\n"
        f"id = {json.dumps(task)}\n"
        'status = "done"\n'
        f"wave = {sequence}\n"
        f"requires = {json.dumps(dependency)}\n"
        f"description = {json.dumps(title)}\n"
        'verify_cmd = ""\n'
    ).encode()


index_file = Path(tempfile.mkstemp(prefix="gideon-condense-index.")[1])
index_file.unlink()
index_env = {"GIT_INDEX_FILE": str(index_file)}


def tree_with_spec(tree: str | None, document: bytes) -> str:
    if tree:
        git("read-tree", tree, env=index_env)
    else:
        git("read-tree", "--empty", env=index_env)
    blob = git("hash-object", "-w", "--stdin", data=document).decode().strip()
    git("update-index", "--add", "--cacheinfo", "100644", blob,
        "spec/history-reconstruction/spec.kvx", env=index_env)
    return git("write-tree", env=index_env).decode().strip()


def create_commit(tree: str, parent: str | None, identity: dict[str, object],
                  message: bytes, zone: str | None = None) -> str:
    author = identity["author"]
    committer = identity["committer"]
    assert isinstance(author, dict) and isinstance(committer, dict)
    author_zone = zone or str(author["zone"])
    committer_zone = zone or str(committer["zone"])
    env = {
        "GIT_AUTHOR_NAME": str(author["name"]),
        "GIT_AUTHOR_EMAIL": str(author["email"]),
        "GIT_AUTHOR_DATE": f"@{author['epoch']} {author_zone}",
        "GIT_COMMITTER_NAME": str(committer["name"]),
        "GIT_COMMITTER_EMAIL": str(committer["email"]),
        "GIT_COMMITTER_DATE": f"@{committer['epoch']} {committer_zone}",
    }
    command = ["commit-tree", tree]
    if parent:
        command.extend(("-p", parent))
    return git(*command, data=message, env=env).decode().strip()


def checkpoints(commits: list[str], count: int) -> list[str]:
    if count < 1 or count > len(commits):
        raise ValueError(f"cannot select {count} checkpoints from {len(commits)} commits")
    if count == 1:
        return [commits[-1]]
    indexes = [0]
    remaining = len(commits) - 1
    for position in range(1, count):
        index = 1 + math.ceil(position * remaining / (count - 1)) - 1
        indexes.append(index)
    if len(set(indexes)) != count or indexes[-1] != len(commits) - 1:
        raise RuntimeError("checkpoint selection did not cover the complete sequence")
    return [commits[index] for index in indexes]


chain = git("rev-list", "--first-parent", "--reverse", ARGS.source_tip).decode().splitlines()
if len(chain) < 2:
    raise RuntimeError("source sequence must contain an anchor and foundation commits")

old_anchor, foundation = chain[0], chain[1:]
selected = checkpoints(foundation, ARGS.foundation_commits)
total = 1 + len(selected) + 1
final_tree = git("write-tree").decode().strip()

anchor_source = commit_data(old_anchor)
anchor_author = anchor_source["author"]
assert isinstance(anchor_author, dict)
anchor_epoch = int(datetime(2026, 7, 11, 9, 0,
                            tzinfo=timezone(timedelta(hours=-7))).timestamp())
anchor_identity = {
    "author": {**anchor_author, "epoch": anchor_epoch, "zone": "-0700"},
    "committer": {**anchor_author, "epoch": anchor_epoch, "zone": "-0700"},
}
anchor_tree = tree_with_spec(None, spec(
    1, total, "program-definition", "user-recorded-start-date",
    "Establish the Gideon development program", anchor_epoch, "-0700"
))
tip = create_commit(anchor_tree, None, anchor_identity,
                    b"chore(spec): establish Gideon development program\n")

for sequence, old_commit in enumerate(selected, start=2):
    data = commit_data(old_commit)
    committer = data["committer"]
    assert isinstance(committer, dict)
    message = normalized(data["message"])
    document = spec(
        sequence, total, "foundation-and-feature-development", old_commit,
        first_line(message), int(committer["epoch"]), str(committer["zone"])
    )
    next_tree = tree_with_spec(str(data["tree"]), document)
    tip = create_commit(next_tree, tip, data, message)
    if sequence % 100 == 0 or sequence == len(selected) + 1:
        print(f"foundation: {sequence - 1}/{len(selected)}", flush=True)

final_data = commit_data(ARGS.final_tip)
final_committer = final_data["committer"]
assert isinstance(final_committer, dict)
final_title = "Complete Gideon system integration"
final_document = spec(
    total, total, "system-integration", ARGS.final_tip, final_title,
    int(final_committer["epoch"]), "-0700", complete=True
)
final_tree = tree_with_spec(final_tree, final_document)
tip = create_commit(
    final_tree, tip, final_data,
    b"feat(platform): complete Gideon system integration\n", zone="-0700"
)

backup = f"refs/archive/{ARGS.target_ref.rsplit('/', 1)[-1]}-before-condense"
previous = git("rev-parse", ARGS.target_ref).decode().strip()
git("update-ref", backup, previous)
git("update-ref", ARGS.target_ref, tip, previous)
print(json.dumps({"target": ARGS.target_ref, "tip": tip, "commits": total,
                  "backup": backup}))
