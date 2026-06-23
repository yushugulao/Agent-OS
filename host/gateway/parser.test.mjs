// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {
  parseAgentOsLine,
  parseAgentOsLog,
  parseKeyValueFields,
  summarizeParsedEntries,
} from "./parser.mjs";

function readFixture(name) {
  return readFileSync(`host/fixtures/${name}`, "utf8");
}

function events(entries) {
  return entries.filter((entry) => entry.kind === "event");
}

const fields = parseKeyValueFields(
  "type=AGENT_STATE role=sentinel state=RUNNING payload=fid=4;status=failed;stage=align;run_id=RUN-042;truncated=0",
);
assert.equal(fields.type, "AGENT_STATE");
assert.equal(fields.role, "sentinel");
assert.equal(fields.state, "RUNNING");
assert.equal(fields.payload,
             "fid=4;status=failed;stage=align;run_id=RUN-042;truncated=0");

const summaryFields = parseKeyValueFields(
  "type=TOOL_CALL role=investigator status=OK summary=memory limit exceeded at align stage",
);
assert.equal(summaryFields.summary, "memory limit exceeded at align stage");

const raw = parseAgentOsLine("labdemo: sentinel state=WAITING", 7);
assert.equal(raw.kind, "raw");
assert.equal(raw.raw, "labdemo: sentinel state=WAITING");

const unknown = parseAgentOsLine(
  "agentos:event type=FUTURE_EVENT status=OK detail=kept",
  8,
);
assert.equal(unknown.kind, "event");
assert.equal(unknown.known, false);
assert.equal(unknown.fields.detail, "kept");

const labdemoEntries = parseAgentOsLog(readFixture("labdemo.log"));
const labdemoSummary = summarizeParsedEntries(labdemoEntries);
const labdemoEvents = events(labdemoEntries);

assert.equal(labdemoSummary.events, 25);
assert.equal(labdemoSummary.finalStatus, "RECOVERED");
assert.equal(labdemoSummary.unknownEvents, 0);
assert.ok(labdemoSummary.rawLogs > 0);
assert.ok(labdemoEvents.some((entry) =>
  entry.type === "TOOL_CALL" &&
  entry.fields.tool === "query_file" &&
  entry.fields.used_index === 1));
assert.ok(labdemoEvents.some((entry) =>
  entry.type === "REPORT" &&
  entry.fields.artifact === "lab_RUN042_recovery_report" &&
  entry.fields.llm_status === "template"));

const labbenchEntries = parseAgentOsLog(readFixture("labbench.log"));
const labbenchSummary = summarizeParsedEntries(labbenchEntries);
const labbenchEvents = events(labbenchEntries);

assert.equal(labbenchSummary.events, 2);
assert.equal(labbenchSummary.finalStatus, null);
assert.equal(labbenchSummary.unknownEvents, 0);
assert.equal(labbenchEvents[0].type, "BENCH");
assert.equal(labbenchEvents[0].fields.case, "file_scan_query");
assert.equal(labbenchEvents[1].fields.case, "duplicate_reject");

console.log("host:test parser=passed labdemo_events=%d labbench_events=%d",
            labdemoSummary.events, labbenchSummary.events);
