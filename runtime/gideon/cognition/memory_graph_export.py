"""Project a memory graph into an inert SVG document with its selected JSON records."""

from __future__ import annotations

import html
import json
import math
from collections import Counter
from functools import reduce
from typing import Any

_W = 1000
_H = 1000
_RINGS: tuple[tuple[int, int], ...] = (
    (1, 0),
    (8, 130),
    (16, 250),
    (35, 370),
    (10_000, 470),
)
_UNCLUSTERED = "hsl(0 0% 62%)"
_MAX_NODES = 600
_MAX_EDGES = 3000


def _ring_segments(count: int):
    remaining = count
    for capacity, radius in _RINGS:
        if remaining <= 0:
            return
        occupancy = min(capacity, remaining)
        yield occupancy, radius
        remaining -= occupancy


def _positions(count: int) -> list[tuple[float, float]]:
    points = []
    for occupancy, radius in _ring_segments(count):
        angles = (
            offset / max(1, occupancy) * math.tau - math.pi / 2
            for offset in range(occupancy)
        )
        points.extend(
            (_W / 2 + math.cos(angle) * radius, _H / 2 + math.sin(angle) * radius)
            for angle in angles
        )
    return points


def _fill(community: Any) -> str:
    if community is not None:
        try:
            label = f"neighbourhood {int(community)}"
        except (TypeError, ValueError):
            return _UNCLUSTERED
        hue = reduce(lambda value, char: (value * 31 + ord(char)) % 360, label, 0)
        return f"hsl({hue} 55% 60%)"
    return _UNCLUSTERED


class _GraphSelection:
    def __init__(self, graph: dict):
        self.nodes = list(graph.get("nodes") or [])
        self.edges = list(graph.get("edges") or [])
        self.notes = []
        if len(self.nodes) > _MAX_NODES:
            self.notes.append(
                f"showing the {_MAX_NODES} most-linked of {len(self.nodes)} entities"
            )
            self.nodes = sorted(
                self.nodes, key=lambda item: -int(item.get("inbound_count") or 0)
            )[:_MAX_NODES]
        identities = {str(node.get("id")) for node in self.nodes}
        self.edges = [
            edge
            for edge in self.edges
            if str(edge.get("from")) in identities and str(edge.get("to")) in identities
        ]
        if len(self.edges) > _MAX_EDGES:
            self.notes.append(
                f"showing the {_MAX_EDGES} strongest of {len(self.edges)} links"
            )
            self.edges = sorted(
                self.edges, key=lambda item: -float(item.get("records") or 0)
            )[:_MAX_EDGES]

    def json(self) -> str:
        return json.dumps(
            dict(nodes=self.nodes, edges=self.edges), indent=1, sort_keys=True
        )

    def legend(self) -> str:
        groups = sorted(
            {
                node.get("community")
                for node in self.nodes
                if node.get("community") is not None
            }
        )
        swatches = [
            (_fill(group), f"neighbourhood {html.escape(str(group))}")
            for group in groups
        ]
        if any(node.get("community") is None for node in self.nodes):
            swatches.append((_UNCLUSTERED, "unclustered"))
        return "".join(
            f'<span><i class="swatch" style="background:{colour}"></i>{label}</span>'
            for colour, label in swatches
        )

    def metadata(self, generated_at: str) -> str:
        values = [f"{len(self.nodes)} entities", f"{len(self.edges)} links"]
        if generated_at:
            values.append(f"exported {html.escape(generated_at)}")
        return " · ".join([*values, *(html.escape(note) for note in self.notes)])


class _SvgGraph:
    def __init__(self, selection: _GraphSelection):
        self.selection = selection
        identifiers = (str(node.get("id")) for node in selection.nodes)
        self.points = dict(zip(identifiers, _positions(len(selection.nodes))))
        self.degree = Counter(
            str(edge.get(end)) for edge in selection.edges for end in ("from", "to")
        )

    def lines(self):
        for edge in self.selection.edges:
            left, right = (
                self.points.get(str(edge.get(end))) for end in ("from", "to")
            )
            if not left or not right:
                continue
            weight = min(3.0, 0.6 + float(edge.get("records") or 1) * 0.35)
            yield (
                f'<line x1="{left[0]:.1f}" y1="{left[1]:.1f}" x2="{right[0]:.1f}" y2="{right[1]:.1f}" '
                f'stroke="#3a444e" stroke-width="{weight:.2f}" stroke-opacity="0.6" />'
            )

    def marks(self):
        for node in self.selection.nodes:
            identity = str(node.get("id"))
            point = self.points.get(identity)
            if not point:
                continue
            radius = min(15.0, 5.0 + self.degree.get(identity, 0) * 1.5)
            label, kind = (
                html.escape(str(value))
                for value in (
                    node.get("name") or identity,
                    node.get("entity_type") or "",
                )
            )
            yield (
                f'<g transform="translate({point[0]:.1f},{point[1]:.1f})">'
                f'<circle r="{radius:.1f}" fill="{_fill(node.get("community"))}" '
                f'fill-opacity="0.9" stroke="#0d1114" stroke-width="1">'
                f"<title>{label} ({kind})</title></circle>"
                f'<text y="{-radius - 4:.1f}" text-anchor="middle" font-size="10" fill="#e3e6ea">'
                f"{label}</text></g>"
            )

    def viewport(self) -> str:
        if not self.points:
            return f"0 0 {_W} {_H}"
        xs, ys = zip(*self.points.values())
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        span = max(max(right - left, bottom - top) + 180, 240)
        center_x, center_y = (left + right) / 2, (top + bottom) / 2
        return (
            f"{center_x - span / 2:.1f} {center_y - span / 2:.1f} {span:.1f} {span:.1f}"
        )

    def render(self) -> str:
        # Evaluate line/node values before viewport and document construction.
        lines, marks = "".join(self.lines()), "".join(self.marks())
        return (
            f'<svg viewBox="{self.viewport()}" xmlns="http://www.w3.org/2000/svg" role="img" '
            'aria-label="Memory entity graph: entities as circles coloured by neighbourhood, '
            'links as lines">\n'
            f"<g>{lines}</g>\n<g>{marks}</g>\n</svg>"
        )


_STYLE = (
    "body{margin:0;background:#101418;color:#e3e6ea;"
    "font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
    "main{max-width:1100px;margin:0 auto;padding:24px}"
    "h1{font-size:1.25rem;margin:0 0 4px}"
    "p.meta{color:#9aa4ae;margin:0 0 16px;font-size:.8125rem}"
    ".legend{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 0;font-size:.8125rem}"
    ".legend span{display:inline-flex;align-items:center;gap:6px;color:#9aa4ae}"
    ".swatch{width:10px;height:10px;border-radius:999px;display:inline-block}"
    "figure{margin:0;border:1px solid #2a3138;border-radius:12px;background:#161b20}"
    "svg{display:block;width:100%;height:auto}"
    "details{margin-top:16px;border:1px solid #2a3138;border-radius:12px;padding:10px 12px}"
    "summary{cursor:pointer;color:#9aa4ae;font-size:.8125rem}"
    "pre{overflow-x:auto;font-size:.75rem;color:#c8cfd6;white-space:pre-wrap}"
    "table{border-collapse:collapse;font-size:.8125rem;margin-top:8px}"
    "td,th{text-align:left;padding:2px 12px 2px 0;color:#c8cfd6}"
)


class _GraphDocument:
    def __init__(self, graph: dict, generated_at: str):
        self.selection = _GraphSelection(graph)
        self.generated_at = generated_at

    def render(self) -> str:
        diagram = _SvgGraph(self.selection).render()
        legend = self.selection.legend()
        metadata = self.selection.metadata(self.generated_at)
        payload = self.selection.json()
        island = payload.replace("--", "-\\u002d")
        parts = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>Gideon memory graph</title>",
            f"<style>{_STYLE}</style>",
            "</head>",
            "<body>",
            "<main>",
            "<h1>Memory entity graph</h1>",
            f'<p class="meta">{metadata}</p>',
            "<figure>",
            diagram,
            "</figure>",
            f'<div class="legend">{legend}</div>',
            "<details><summary>Underlying data (JSON)</summary>",
            f"<pre>{html.escape(payload)}</pre>",
            "</details>",
            f"<!-- gideon-memory-graph\n{island}\n-->",
            "</main>",
            "</body>",
            "</html>",
            "",
        ]
        return "\n".join(parts)


def render_graph_html(graph: dict, *, generated_at: str = "") -> str:
    document = _GraphDocument(graph, generated_at).render()
    from gideon.cognition.knowledge.reports import assert_self_contained

    assert_self_contained(document)
    return document
