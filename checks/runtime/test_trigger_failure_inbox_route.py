from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from gideon.automation.triggers.delivery import build_delivery, deliver
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.integrations.inbox import InboxStore
from gideon.integrations.inbox_service import InboxService
from gideon.interfaces.dashboard.state import ConsoleState


def _run_console_route_helpers(repo_root: Path, tmp_path: Path, refs: dict):
    notification_source = (
        repo_root / "apps/console/src/features/notifications/notificationMeta.ts"
    ).read_text()
    inbox_source = (
        repo_root / "apps/console/src/features/inbox/inboxMeta.ts"
    ).read_text()
    notification_start = notification_source.index("function routeIdentity(")
    notification_end = notification_source.index(
        "export function toneChipBg(", notification_start
    )
    inbox_start = inbox_source.index("type ItemReference =")
    inbox_end = inbox_source.index("export function channelLabel(", inbox_start)
    helpers = "\n".join(
        (
            notification_source[notification_start:notification_end],
            inbox_source[inbox_start:inbox_end],
        )
    )
    node = shutil.which("node")
    assert node, "Node is required to execute the console TypeScript route helpers"
    helper_path = tmp_path / "inbox_route_helpers.ts"
    helper_path.write_text(helpers)
    driver_path = tmp_path / "inbox_route_helpers.cjs"
    driver_path.write_text("""const fs = require('node:fs');
const ts = require(require.resolve('typescript', { paths: [process.cwd()] }));
const source = fs.readFileSync(process.argv[2], 'utf8');
const emitted = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const helperModule = { exports: {} };
new Function('module', 'exports', emitted)(helperModule, helperModule.exports);
const { notificationLink, refTarget } = helperModule.exports;
const persistedRefs = JSON.parse(process.argv[3]);
const routeCases = [
  '#/triggers?open=schedule%3Aweekly%2Freport',
  '#/workflows/runs/run%2F2026%20weekly',
  'https://evil.example/#/workflows/runs/run-1',
  '#/tasks/run-1',
  '#/workflows/runs/%E0%A4%A',
  '#/workflows/runs/run-1?next=https://evil.example',
  '#/triggers?open=one&next=two',
  '#/workflows/runs/',
];
const typedRefs = [
  { session: 'a/b c', statusUrl: '#/triggers?open=trigger-1' },
  { workflow: 'workflow-1', statusUrl: '#/workflows/runs/run-1' },
  { artifact: 'report-1', statusUrl: '#/workflows/runs/run-1' },
];
process.stdout.write(JSON.stringify({
  routes: routeCases.map(notificationLink),
  persistedItem: refTarget({ refs: persistedRefs }),
  typedRefs: typedRefs.map(refs => refTarget({ refs })),
}));
""")
    return subprocess.run(
        [node, str(driver_path), str(helper_path), json.dumps(refs)],
        cwd=repo_root / "apps/console",
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_failed_trigger_inbox_item_opens_allowlisted_trigger_or_run(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_CRON_FAILED_NOTIFICATIONS", "true")
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0.0)
    inbox = InboxStore(path=tmp_path / "inbox.json")
    inbox.load()
    state._inbox_svc = InboxService(store=inbox)

    delivery = build_delivery(
        trigger_id="schedule:weekly/report",
        trigger_name="Weekly report",
        ok=False,
        summary="The workflow run failed.",
        run_id="run/2026 weekly",
        destination="inbox",
    )

    assert deliver(state, delivery)
    assert deliver(state, delivery)

    monkeypatch.setenv("GIDEON_CRON_FAILED_NOTIFICATIONS", "false")
    ordinary_delivery = build_delivery(
        trigger_id="webhook:invoice",
        trigger_name="Invoice webhook",
        ok=False,
        summary="The webhook run failed.",
        run_id="run-ordinary",
        destination="inbox",
    )
    assert ordinary_delivery.kind == "error"
    assert deliver(state, ordinary_delivery)
    assert deliver(state, ordinary_delivery)

    persisted = InboxStore(path=tmp_path / "inbox.json")
    persisted.load()
    assert len(persisted.items) == 2
    item = next(
        item
        for item in persisted.items.values()
        if item.refs.get("trigger_id") == "schedule:weekly/report"
    )
    assert item.refs == {
        "trigger_id": "schedule:weekly/report",
        "run_id": "run/2026 weekly",
        "statusUrl": "#/workflows/runs/run%2F2026%20weekly",
        "event_id": delivery.event_id,
        "dedup_key": "trigger_failure:schedule:weekly/report:run/2026 weekly",
    }
    assert len(state._notification_log) == 2
    notifications_by_kind = {entry["kind"]: entry for entry in state._notification_log}
    assert notifications_by_kind["cron_failed"]["inbox_item"] == item.id
    ordinary_item = next(
        item
        for item in persisted.items.values()
        if item.refs.get("trigger_id") == "webhook:invoice"
    )
    assert ordinary_item.refs["run_id"] == "run-ordinary"
    assert ordinary_item.refs["statusUrl"] == "#/workflows/runs/run-ordinary"
    assert notifications_by_kind["error"]["inbox_item"] == ordinary_item.id

    route_run = _run_console_route_helpers(
        Path(__file__).resolve().parents[2], tmp_path, item.refs
    )
    assert route_run.returncode == 0, route_run.stderr
    route_results = json.loads(route_run.stdout.strip())
    assert route_results["routes"] == [
        "triggers?open=schedule%3Aweekly%2Freport",
        "workflows/runs/run%2F2026%20weekly",
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    assert route_results["persistedItem"] == "workflows/runs/run%2F2026%20weekly"
    assert route_results["typedRefs"] == [
        "chat/a%2Fb%20c",
        "workflows/workflow-1",
        "artifacts/report-1",
    ]
