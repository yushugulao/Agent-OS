// SPDX-License-Identifier: Apache-2.0

import {readFileSync} from "node:fs";
import {basename, resolve} from "node:path";
import {generateLlmAnalysis} from "./llm.mjs";
import {parseAgentOsLog, summarizeParsedEntries} from "./parser.mjs";

export const defaultFixtures = [
  "host/fixtures/labdemo.log",
  "host/fixtures/labbench.log",
];

export function loadReplayFiles(files = defaultFixtures) {
  return files.map((file) => {
    const text = readFileSync(resolve(file), "utf8");
    const entries = parseAgentOsLog(text);
    return {
      file,
      name: basename(file),
      entries,
      summary: summarizeParsedEntries(entries),
    };
  });
}

export async function buildReplayEvents(files = defaultFixtures, llmConfig = {},
                                        fetchImpl = globalThis.fetch) {
  const loaded = loadReplayFiles(files);
  const entries = loaded.flatMap((item) => item.entries);
  const events = entries.filter((entry) => entry.kind === "event");
  const llmEvent = await generateLlmAnalysis(entries, llmConfig, fetchImpl);
  const allEvents = [...events, llmEvent];
  const finalEvent = events.findLast((entry) => entry.type === "FINAL");

  return {
    files: loaded,
    entries,
    events: allEvents,
    llmEvent,
    finalStatus: finalEvent ? finalEvent.fields.status : null,
    summary: {
      totalEvents: allEvents.length,
      parsedEvents: events.length,
      finalStatus: finalEvent ? finalEvent.fields.status : null,
      llmMode: llmEvent.fields.mode,
    },
  };
}
