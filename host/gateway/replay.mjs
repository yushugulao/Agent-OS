// SPDX-License-Identifier: Apache-2.0

import {buildReplayEvents, defaultFixtures} from "./replay-data.mjs";

function printFileSummary(item) {
  console.log(
    "host:replay file=%s lines=%d events=%d raw=%d unknown=%d final=%s",
    item.name,
    item.summary.lines,
    item.summary.events,
    item.summary.rawLogs,
    item.summary.unknownEvents,
    item.summary.finalStatus || "none",
  );

  for (const entry of item.entries) {
    if (entry.kind !== "event")
      continue;
    console.log(JSON.stringify({
      line: entry.lineNumber,
      type: entry.type,
      known: entry.known,
      fields: entry.fields,
    }));
  }
}

const files = process.argv.slice(2);
const targets = files.length > 0 ? files : defaultFixtures;
const replay = await buildReplayEvents(targets);

for (const item of replay.files)
  printFileSummary(item);

const llmEvent = replay.llmEvent;
console.log(JSON.stringify({
  line: llmEvent.lineNumber,
  type: llmEvent.type,
  known: llmEvent.known,
  fields: llmEvent.fields,
}));

console.log("host:replay total_events=%d final=%s llm=%s",
            replay.summary.totalEvents, replay.finalStatus || "none",
            llmEvent.fields.mode);
