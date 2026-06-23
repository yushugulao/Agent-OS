// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import {createGatewayServer} from "./server.mjs";

const gateway = await createGatewayServer({
  port: 0,
  llmConfig: {apiKey: "", providerName: "test-provider"},
});
await new Promise((resolve) => {
  gateway.server.listen(0, "127.0.0.1", resolve);
});
const {port} = gateway.server.address();
const base = `http://127.0.0.1:${port}`;

try {
  const health = await fetch(`${base}/health`).then((res) => res.json());
  assert.equal(health.ok, true);
  assert.equal(health.mode, "replay");
  assert.equal(health.final, "RECOVERED");
  assert.ok(["fallback", "cloud"].includes(health.llm));

  const replay = await fetch(`${base}/api/replay`).then((res) => res.json());
  assert.equal(replay.mode, "replay");
  assert.equal(replay.summary.finalStatus, "RECOVERED");
  assert.equal(replay.events.length, 28);
  assert.ok(replay.events.some((event) => event.type === "LLM_ANALYSIS"));
  assert.ok(replay.events.filter((event) => event.type === "AGENT_CREATED")
    .length >= 4);
  assert.ok(replay.events.some((event) =>
    event.type === "BENCH" && event.fields.case === "file_scan_query"));

  const streamResponse = await fetch(`${base}/events?interval=1`);
  assert.equal(streamResponse.ok, true);
  const streamText = await streamResponse.text();
  assert.match(streamText, /type":"LAB_INIT"/);
  assert.match(streamText, /type":"LLM_ANALYSIS"/);
  assert.match(streamText, /event: done/);

  console.log("host:test dashboard=passed events=%d final=%s llm=%s",
              replay.events.length, replay.summary.finalStatus,
              replay.summary.llmMode);
} finally {
  await gateway.close();
}
