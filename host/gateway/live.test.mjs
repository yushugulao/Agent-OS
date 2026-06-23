// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import {createLiveSource} from "./live-source.mjs";

const isWindows = process.platform === "win32";
const command = isWindows ?
  [
    "powershell",
    "-NoProfile",
    "-Command",
    "\"Write-Output 'agentos:event type=LAB_INIT project=lab-gene-x workflow=nightly-regression run_id=RUN-042';",
    "Write-Output 'agentos:event type=AGENT_CREATED role=sentinel pid=7 context=0x3ffffb000';",
    "Write-Output 'agentos:event type=FINAL status=RECOVERED';",
    "Write-Output 'labdemo: passed'\"",
  ].join(" ") :
  [
    "printf '%s\\n'",
    "'agentos:event type=LAB_INIT project=lab-gene-x workflow=nightly-regression run_id=RUN-042'",
    "'agentos:event type=AGENT_CREATED role=sentinel pid=7 context=0x3ffffb000'",
    "'agentos:event type=FINAL status=RECOVERED'",
    "'labdemo: passed'",
  ].join(" ");

const source = createLiveSource({
  command,
  runBench: false,
  bootWaitMs: 0,
  timeoutMs: 5000,
  llmConfig: {apiKey: "", providerName: "test-provider"},
  autoExitQemu: false,
}).start();

const summary = await source.waitUntilDone();
assert.equal(summary.status, "done");
assert.equal(summary.finalStatus, "RECOVERED");
assert.equal(summary.llmMode, "fallback");
assert.ok(source.getEvents().some((event) => event.type === "LLM_ANALYSIS"));
assert.ok(source.getEvents().some((event) => event.type === "AGENT_CREATED"));
await source.close();

console.log("host:test live=passed events=%d final=%s llm=%s",
            summary.totalEvents, summary.finalStatus, summary.llmMode);
