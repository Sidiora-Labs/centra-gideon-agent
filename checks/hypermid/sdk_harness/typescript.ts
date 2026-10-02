import { HypermidOutcomeUnknown, HypermidRemoteError, type Cursor, type Json, type Scope } from "../../../clients/hypermid-ts/src/client.js";
import { connectNode, loadConnectionRecord } from "../../../clients/hypermid-ts/src/node.js";

async function main(): Promise<void> {
  const [recordPath, invalidRecordPath] = process.argv.slice(2);
  if (!recordPath || !invalidRecordPath) throw new Error("record paths are required");
  const scope: Scope = { owner_id: "sdk-owner", project_id: "sdk-project", workspace_id: "sdk-workspace" };

  let invalidVersionRefused = false;
  try { loadConnectionRecord(invalidRecordPath); }
  catch { invalidVersionRefused = true; }
  if (!invalidVersionRefused) throw new Error("invalid protocol connection record was accepted");

  const first = await connectNode(recordPath, scope);
  const description = await first.client.describe();
  if (!isObject(description) || description.protocol !== "hypermid.v1") throw new Error("describe did not report hypermid.v1");
  const marker: Json = { language: "typescript", value: 7 };
  if (JSON.stringify(await first.client.passthrough(marker)) !== JSON.stringify(marker)) throw new Error("passthrough payload changed");
  await first.client.request("events.publish", {
    event_id: "sdk-typescript-event",
    topic: "events.sdk",
    scope: { owner_id: scope.owner_id, project_id: scope.project_id, workspace_id: scope.workspace_id ?? null },
    at_ms: 2,
    schema_name: "sdk.client",
    schema_version: 1,
    payload: marker,
  }, { effect: "mutation" });
  const consumerId = "sdk-typescript-consumer";
  const subscribed = await first.client.subscribeEvents({ consumerId, scope, topicFilter: "events.*" });
  const event = await subscribed.nextEvent();
  if (!(await subscribed.acknowledge(event)).acknowledged) throw new Error("durable event was not acknowledged");
  const cursor = subscribed.resumePoint.cursor;
  await subscribed.close();
  await first.client.close();

  const second = await connectNode(recordPath, scope);
  const resumed = await second.client.resumeEvents({ consumerId, scope, topicFilter: "events.*", after: cursor });
  const resumedCursor = resumed.resumePoint.cursor;
  if (resumedCursor.epoch !== cursor.epoch || resumedCursor.sequence !== cursor.sequence) throw new Error("cursor resume changed");
  let wrongEpochRefused = false;
  try {
    await second.client.resumeEvents({
      consumerId,
      scope,
      topicFilter: "events.*",
      after: { epoch: cursor.epoch + 1, sequence: cursor.sequence },
    });
  }
  catch (error) {
    wrongEpochRefused = error instanceof HypermidRemoteError
      && ["CURSOR_GAP", "CONSUMER_RESUME_MISMATCH"].includes(error.detail.code);
  }
  if (!wrongEpochRefused) throw new Error("foreign cursor epoch was not refused");
  await resumed.close();
  await second.client.close();

  const mutation = await connectNode(recordPath, scope);
  mutation.transport.disconnectAfterNextWrite();
  let outcomeUnknown = false;
  try {
    await mutation.client.request("diagnostics.rerun", {}, { effect: "mutation" });
  } catch (error) {
    outcomeUnknown = error instanceof HypermidOutcomeUnknown;
  }
  if (!outcomeUnknown) throw new Error("mutation disconnect did not return outcome unknown");

  process.stdout.write(JSON.stringify({
    language: "typescript",
    tls: true,
    describe: true,
    passthrough: true,
    durable_ack: true,
    reconnect_resume: true,
    wrong_epoch_refused: true,
    invalid_version_refused: true,
    mutation_outcome_unknown: true,
  }) + "\n");
}

function isObject(value: Json): value is { [key: string]: Json } {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

await main();
