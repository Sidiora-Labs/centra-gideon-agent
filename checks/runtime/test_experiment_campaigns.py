from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gideon.assurance.experiments import campaigns, machines, replay, strategy
from gideon.automation.workflows import defs, journal, service, store
from gideon.automation.workflows.models import InstanceState, RunStatus, WorkflowRun
from gideon.automation.workflows.native_defs import register_native_provider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.core.config import loader


class ExperimentPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home_patch = patch.object(loader, "config_dir", return_value=self.root)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.temp.cleanup()

    def test_campaign_observation_and_recorded_run_divergence(self):
        campaign = campaigns.create({
            "title": "Compare inputs", "objective": "Measure completed workflow outcomes",
            "workflow_name": "sample", "metric": "quality", "direction": "maximize",
            "variants": [{"topic": "alpha"}, {"topic": "beta"}],
            "max_parallel": 1, "max_tokens": 1000,
        })
        run = store.create(WorkflowRun(id="", workflow_name="sample", status=RunStatus.COMPLETE, inputs={"topic": "alpha"}))
        store.write_spec(run.id, {"root": {"kind": "agent", "id": "answer"}})
        writer = journal.Journal(run.id)
        writer.run_started("sample", inputs=run.inputs, spec_version=1)
        writer.step_started("root.answer", "answer", epoch=0, lane="agent")
        writer.step_completed("root.answer", "answer", epoch=0, cache_key="first", state=InstanceState.DONE, tokens=4)
        writer.run_finished("complete")
        with campaigns._db() as db:
            db.execute("UPDATE attempts SET run_id=?, state='complete' WHERE campaign_id=? AND ordinal=0", (run.id, campaign["id"]))
        observed = campaigns.observe(campaign["id"], 0, score=0.8, valid=True, observation="Output met the rubric")
        self.assertEqual(observed["best_attempt"], 0)
        self.assertEqual(observed["attempts"][0]["run_id"], run.id)
        self.assertEqual(observed["attempts"][1]["state"], "queued")

        cassette = replay.capture(run.id)
        initial = replay.compare(cassette["id"])
        self.assertTrue(initial["matches"])
        self.assertFalse(initial["side_effects_executed"])
        self.assertEqual(initial["candidate_steps"][0]["state"], "done")
        writer.step_started("root.followup", "followup", epoch=0, lane="tool")
        changed = replay.compare(cassette["id"])
        self.assertFalse(changed["matches"])
        self.assertTrue(any(row["path"].startswith("steps[1]") for row in changed["divergence"]))

    def test_local_machine_registry_is_durable_and_probes_real_host(self):
        machines.add({"id": "here", "kind": "local", "slots": 2})
        self.assertEqual(machines.list_machines(), [{"id": "here", "kind": "local", "slots": 2}])
        self.assertTrue(machines.probe("here")["reachable"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            machines.add({"id": "here", "kind": "local", "slots": 1})
        machines.remove("here")
        self.assertEqual(json.loads((self.root / "experiments" / "machines.json").read_text()), [])

    def test_campaign_launches_and_recovers_a_real_workflow_run(self):
        had_native = defs.get_provider("native") is not None
        register_native_provider()

        async def journey():
            saved = await service.author_def(
                name="experiment-run", provenance="user", strict=False,
                root={"kind": "sequence", "id": "main", "children": [
                    {"kind": "transform", "id": "answer", "config": {"expr": "measured"}},
                ]},
            )
            self.assertTrue(saved["ok"], saved)
            campaign = campaigns.create({
                "title": "Run a real workflow", "objective": "Observe the completed result",
                "workflow_name": "experiment-run", "metric": "quality", "direction": "maximize",
                "variants": [{}], "max_parallel": 1, "max_tokens": 1000,
            })
            supervisor = WorkflowWatchdog()
            try:
                launched = await campaigns.advance(campaign["id"], supervisor=supervisor)
                run_id = launched["attempts"][0]["run_id"]
                self.assertTrue(run_id)
                controller = supervisor.controller(run_id)
                self.assertIsNotNone(controller)
                self.assertEqual(await controller.wait_for_terminal(timeout=5), RunStatus.COMPLETE)
                recovered = campaigns.detail(campaign["id"])
                self.assertEqual(recovered["attempts"][0]["state"], "complete")
                self.assertEqual(recovered["attempts"][0]["run_id"], run_id)
                self.assertEqual(len([a for a in campaigns.listing() if a["id"] == campaign["id"]]), 1)
            finally:
                await supervisor.stop()

        try:
            asyncio.run(journey())
        finally:
            if not had_native:
                defs.unregister_provider("native")

    def test_offline_workspace_executes_a_real_local_job_and_retains_result(self):
        scenario = Path(__file__).resolve().parents[2] / "runtime/gideon/assurance/evals/library/smoke_test.json"
        report = strategy.run({
            "kind": "local_job", "objective": "Check candidate scenario input",
            "candidates": [{"name": "parse-scenario", "command": [sys.executable, "-m", "json.tool", str(scenario)]}],
            "trials": 1, "timeout_seconds": 15,
        })
        attempt = report["attempts"][0]
        self.assertEqual(attempt["status"], "complete")
        self.assertTrue(attempt["valid"])
        self.assertEqual(attempt["score"], 1.0)
        self.assertTrue((Path(attempt["workspace"]) / "stdout.log").is_file())
        persisted = self.root / "evals" / "strategies" / report["id"] / "report.json"
        self.assertEqual(json.loads(persisted.read_text())["attempts"][0]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
