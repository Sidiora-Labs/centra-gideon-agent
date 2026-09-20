import re
from pathlib import Path

from gideon.integrations.inbox import STATUS_CLOSED, STATUS_OPEN, ItemStatus


def test_typescript_and_python_open_statuses_match():
    source = (
        Path(__file__).parents[2] / "apps/console/src/shared/data/attentionLanes.ts"
    ).read_text(encoding="utf-8")
    body = re.search(
        r"export const STATUS_OPEN[^=]*=\s*\{(?P<body>.*?)\n\}", source, re.DOTALL
    )
    assert body is not None
    frontend_open = set(re.findall(r"^\s*(\w+):\s*true,?$", body["body"], re.MULTILINE))
    assert frontend_open == STATUS_OPEN


def test_every_item_status_is_classified_open_or_closed():
    statuses = {status.value for status in ItemStatus}
    assert STATUS_OPEN.isdisjoint(STATUS_CLOSED)
    assert STATUS_OPEN | STATUS_CLOSED == statuses
