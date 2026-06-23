// SPDX-License-Identifier: Apache-2.0

const EVENT_PREFIX = "agentos:event ";
const KEY_PATTERN = /(^|\s)([A-Za-z_][A-Za-z0-9_]*)=/g;

export const knownEventTypes = new Set([
  "LAB_INIT",
  "AGENT_CREATED",
  "WATCH_REGISTERED",
  "AGENT_STATE",
  "INCIDENT_CREATED",
  "TOOL_CALL",
  "MESSAGE",
  "AUDIT",
  "ACTION",
  "CONTEXT_SNAPSHOT",
  "REPORT",
  "FINAL",
  "BENCH",
  "LLM_ANALYSIS",
]);

function parseScalar(value) {
  if (/^-?[0-9]+$/.test(value))
    return Number(value);
  return value;
}

export function parseKeyValueFields(text) {
  const matches = [];
  let match;

  KEY_PATTERN.lastIndex = 0;
  while ((match = KEY_PATTERN.exec(text)) !== null) {
    const keyStart = match.index + match[1].length;
    const key = match[2];
    const valueStart = keyStart + key.length + 1;
    matches.push({key, keyStart, valueStart});
  }

  const fields = {};
  for (let i = 0; i < matches.length; i++) {
    const current = matches[i];
    const next = matches[i + 1];
    const valueEnd = next ? next.keyStart : text.length;
    const rawValue = text.slice(current.valueStart, valueEnd).trim();
    fields[current.key] = parseScalar(rawValue);
  }

  return fields;
}

export function parseAgentOsLine(line, lineNumber = 0) {
  const text = line.replace(/\r?\n$/, "");
  const prefixAt = text.indexOf(EVENT_PREFIX);

  if (prefixAt < 0) {
    return {
      kind: "raw",
      lineNumber,
      raw: text,
    };
  }

  const payload = text.slice(prefixAt + EVENT_PREFIX.length).trim();
  const fields = parseKeyValueFields(payload);
  const type = fields.type ? String(fields.type) : "UNKNOWN";

  return {
    kind: "event",
    lineNumber,
    type,
    known: knownEventTypes.has(type),
    fields,
    raw: text,
  };
}

export function parseAgentOsLog(text) {
  return text.split(/\r?\n/).filter((line) => line.length > 0)
    .map((line, index) => parseAgentOsLine(line, index + 1));
}

export function summarizeParsedEntries(entries) {
  const events = entries.filter((entry) => entry.kind === "event");
  const rawLogs = entries.filter((entry) => entry.kind === "raw");
  const unknownEvents = events.filter((entry) => !entry.known);
  const finalEvent = events.findLast((entry) => entry.type === "FINAL");

  return {
    lines: entries.length,
    events: events.length,
    rawLogs: rawLogs.length,
    unknownEvents: unknownEvents.length,
    finalStatus: finalEvent ? finalEvent.fields.status : null,
  };
}
