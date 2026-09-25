"""Generate Gideon's original articulated robot model and motion clips."""

import json
import struct
from pathlib import Path


def build_robot():
    binary = bytearray()
    views, accessors = [], []

    def attribute(values, components, kind="f", component_type=5126):
        while len(binary) % 4:
            binary.append(0)
        start = len(binary)
        flat = [number for value in values for number in value]
        binary.extend(struct.pack("<" + kind * len(flat), *flat))
        views.append(
            {"buffer": 0, "byteOffset": start, "byteLength": len(binary) - start}
        )
        accessor = {
            "bufferView": len(views) - 1,
            "componentType": component_type,
            "count": len(values),
            "type": {1: "SCALAR", 3: "VEC3", 4: "VEC4"}[components],
        }
        if components in (1, 3):
            accessor.update(
                min=[min(row[i] for row in values) for i in range(components)],
                max=[max(row[i] for row in values) for i in range(components)],
            )
        accessors.append(accessor)
        return len(accessors) - 1

    positions = [
        (-0.5, -0.5, -0.5),
        (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5),
        (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
        (-0.5, 0.5, 0.5),
    ]
    indices = [
        (n,)
        for n in [
            0,
            2,
            1,
            0,
            3,
            2,
            4,
            5,
            6,
            4,
            6,
            7,
            0,
            1,
            5,
            0,
            5,
            4,
            2,
            3,
            7,
            2,
            7,
            6,
            0,
            4,
            7,
            0,
            7,
            3,
            1,
            2,
            6,
            1,
            6,
            5,
        ]
    ]
    pos, ind = attribute(positions, 3), attribute(indices, 1, "H", 5123)
    meshes = [
        {
            "name": name,
            "primitives": [
                {"attributes": {"POSITION": pos}, "indices": ind, "material": i}
            ],
        }
        for i, name in enumerate(["blue shell", "warm face", "dark eyes"])
    ]
    nodes = [
        {"name": "Gideon robot", "children": [1, 2, 3, 5, 7, 8]},
        {"name": "Torso", "mesh": 0, "scale": [1, 1.4, 0.6]},
        {
            "name": "Head",
            "mesh": 1,
            "translation": [0, 1.15, 0],
            "scale": [0.85, 0.75, 0.75],
            "children": [9, 10],
        },
        {"name": "Left shoulder", "translation": [-0.7, 0.6, 0], "children": [4]},
        {
            "name": "Left arm",
            "mesh": 0,
            "translation": [0, -0.45, 0],
            "scale": [0.3, 1, 0.3],
        },
        {"name": "Right shoulder", "translation": [0.7, 0.6, 0], "children": [6]},
        {
            "name": "Right arm",
            "mesh": 0,
            "translation": [0, -0.45, 0],
            "scale": [0.3, 1, 0.3],
        },
        {
            "name": "Left leg",
            "mesh": 0,
            "translation": [-0.3, -1.1, 0],
            "scale": [0.35, 0.85, 0.45],
        },
        {
            "name": "Right leg",
            "mesh": 0,
            "translation": [0.3, -1.1, 0],
            "scale": [0.35, 0.85, 0.45],
        },
        {
            "name": "Left eye",
            "mesh": 2,
            "translation": [-0.22, 0.1, 0.51],
            "scale": [0.14, 0.14, 0.08],
        },
        {
            "name": "Right eye",
            "mesh": 2,
            "translation": [0.22, 0.1, 0.51],
            "scale": [0.14, 0.14, 0.08],
        },
    ]
    times = attribute([(0,), (0.6,), (1.2,)], 1)
    motions = {
        "idle": (2, [0, 0.06, 0, 0.998]),
        "working": (3, [0.35, 0, 0, 0.93675]),
        "needs_input": (2, [0, 0, 0.12, 0.99277]),
        "waiting_approval": (5, [0.15, 0, 0, 0.98869]),
        "error": (2, [0, 0.2, 0, 0.9798]),
        "speaking": (2, [0.1, 0, 0, 0.99499]),
    }
    animations = []
    for name, (node, rotation) in motions.items():
        length = sum(value * value for value in rotation) ** 0.5
        rotation = [value / length for value in rotation]
        output = attribute([(0, 0, 0, 1), rotation, (0, 0, 0, 1)], 4)
        animations.append(
            {
                "name": name,
                "samplers": [
                    {"input": times, "output": output, "interpolation": "LINEAR"}
                ],
                "channels": [
                    {"sampler": 0, "target": {"node": node, "path": "rotation"}}
                ],
            }
        )
    colors = [[0.08, 0.35, 0.65, 1], [0.85, 0.66, 0.28, 1], [0.015, 0.03, 0.06, 1]]
    document = {
        "asset": {"version": "2.0", "generator": "Gideon articulated robot"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": [
            {
                "pbrMetallicRoughness": {
                    "baseColorFactor": color,
                    "metallicFactor": 0.15,
                    "roughnessFactor": 0.65,
                },
                "doubleSided": False,
            }
            for color in colors
        ],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": accessors,
        "animations": animations,
    }
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * ((-len(encoded)) % 4)
    binary.extend(b"\0" * ((-len(binary)) % 4))
    return (
        struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(encoded) + 8 + len(binary))
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


if __name__ == "__main__":
    Path(__file__).with_name("robot.glb").write_bytes(build_robot())
