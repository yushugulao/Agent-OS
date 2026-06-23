// SPDX-License-Identifier: Apache-2.0

import {existsSync, readFileSync} from "node:fs";

function cleanText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function parseBool(value, fallback) {
  if (value === undefined || value === "")
    return fallback;
  return !["0", "false", "no", "off"].includes(String(value).toLowerCase());
}

export function readEnvFile(path = ".env") {
  if (!existsSync(path))
    return {};

  const env = {};
  const lines = readFileSync(path, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#"))
      continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0)
      continue;
    const key = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim();
    env[key] = value;
  }
  return env;
}

export function loadGatewayConfig(env = process.env, envPath = ".env") {
  const fileEnv = readEnvFile(envPath);
  const merged = {...fileEnv, ...env};
  const apiBaseUrl = (merged.LLM_API_BASE_URL ||
    "https://api.openai.com/v1").replace(/\/+$/, "");

  return {
    apiBaseUrl,
    apiKey: merged.LLM_API_KEY || "",
    model: merged.LLM_MODEL || "gpt-4o-mini",
    providerName: merged.LLM_PROVIDER_NAME || "openai-compatible",
    offlineFallback: parseBool(merged.LLM_OFFLINE_FALLBACK, true),
    timeoutMs: Number(merged.LLM_TIMEOUT_MS || 8000),
  };
}

export function collectGatewayState(entries) {
  const events = entries.filter((entry) => entry.kind === "event");
  const byType = (type) => events.filter((entry) => entry.type === type);
  const firstTool = (tool) => events.find((entry) =>
    entry.type === "TOOL_CALL" && entry.fields.tool === tool);

  const agents = {};
  for (const entry of byType("AGENT_CREATED")) {
    agents[entry.fields.role] = {
      pid: entry.fields.pid,
      context: entry.fields.context,
      state: "CREATED",
    };
  }
  for (const entry of byType("AGENT_STATE")) {
    if (agents[entry.fields.role])
      agents[entry.fields.role].state = entry.fields.state;
  }

  return {
    lab: byType("LAB_INIT").at(-1)?.fields || {},
    incident: byType("INCIDENT_CREATED").at(-1)?.fields || {},
    agents,
    queryFile: firstTool("query_file")?.fields || {},
    summary: firstTool("read_file_summary")?.fields || {},
    dependency: firstTool("dependency_query")?.fields || {},
    actions: byType("ACTION").map((entry) => entry.fields),
    audits: byType("AUDIT").map((entry) => entry.fields),
    report: byType("REPORT").at(-1)?.fields || {},
    final: byType("FINAL").at(-1)?.fields || {},
  };
}

function fallbackFields(entries, providerName, reason) {
  const state = collectGatewayState(entries);
  const project = state.lab.project || "unknown-project";
  const runId = state.lab.run_id || "unknown-run";
  const stage = state.incident.stage || state.queryFile.stage || "unknown";
  const rootCause = state.summary.summary ||
    (state.incident.reason ? `${state.incident.reason} at ${stage} stage` :
      "failed artifact requires investigation");
  const impact = state.dependency.impact || "unknown";
  const finalStatus = state.final.status || "UNKNOWN";
  const report = state.report.artifact || "recovery report";

  return {
    type: "LLM_ANALYSIS",
    mode: "fallback",
    provider: providerName || "offline-template",
    status: "OK",
    reason,
    summary:
      `${project}/${runId} 经过 Agent 协同恢复后达到 ${finalStatus} 状态。`,
    root_cause: rootCause,
    recommended_action:
      `建议将重跑范围限制在 ${impact}，并继续保留 Sentinel 和 Recovery 的权限边界。`,
    risk:
      finalStatus === "RECOVERED" ?
        "低：恢复流程已完成，但仍需持续观察 align 阶段是否重复触发内存限制。" :
        "中：最终恢复状态尚未确认，需要继续排查。",
    evidence_refs:
      `incident:${state.incident.id || stage},artifact:${state.summary.artifact || "unknown"},impact:${impact},report:${report}`,
  };
}

function analysisEvent(fields) {
  return {
    kind: "event",
    lineNumber: 0,
    type: "LLM_ANALYSIS",
    known: true,
    fields: {type: "LLM_ANALYSIS", ...fields},
    raw: "host:llm LLM_ANALYSIS",
  };
}

function stripJsonFence(text) {
  const trimmed = text.trim();
  if (!trimmed.startsWith("```"))
    return trimmed;
  return trimmed.replace(/^```[A-Za-z0-9_-]*\s*/, "").replace(/\s*```$/, "");
}

function normalizeCloudFields(parsed, config) {
  return {
    mode: "cloud",
    provider: config.providerName,
    status: "OK",
    summary: cleanText(parsed.summary),
    root_cause: cleanText(parsed.root_cause),
    recommended_action: cleanText(parsed.recommended_action),
    risk: cleanText(parsed.risk),
    evidence_refs: cleanText(parsed.evidence_refs),
  };
}

function buildPrompt(entries) {
  const state = collectGatewayState(entries);
  return [
    "请为 Agent-OS 比赛演示返回一个中文 JSON 对象。",
    "Required fields: summary, root_cause, recommended_action, risk, evidence_refs.",
    "summary、root_cause、recommended_action、risk 必须使用中文。",
    "evidence_refs 可以保留事件名、工具名、工件名等英文标识。",
    "Do not include markdown fences.",
    `State: ${JSON.stringify(state)}`,
  ].join("\n");
}

export async function generateLlmAnalysis(entries, config = {},
                                          fetchImpl = globalThis.fetch) {
  const effective = {
    ...loadGatewayConfig(),
    ...config,
  };

  if (!effective.apiKey) {
    return analysisEvent(fallbackFields(entries, effective.providerName,
                                        "missing_api_key"));
  }

  if (typeof fetchImpl !== "function") {
    return analysisEvent(fallbackFields(entries, effective.providerName,
                                        "fetch_unavailable"));
  }

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), effective.timeoutMs);
    let response;
    try {
      response = await fetchImpl(`${effective.apiBaseUrl}/chat/completions`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "authorization": `Bearer ${effective.apiKey}`,
        },
        body: JSON.stringify({
          model: effective.model,
          temperature: 0.2,
          messages: [
            {
              role: "system",
              content:
                "你正在为操作系统比赛评委总结 Agent-OS 内核演示，回答必须清晰、简洁、中文优先。",
            },
            {role: "user", content: buildPrompt(entries)},
          ],
        }),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }

    if (!response.ok)
      throw new Error(`llm_http_${response.status}`);

    const data = await response.json();
    const content = data?.choices?.[0]?.message?.content;
    if (!content)
      throw new Error("llm_empty_content");

    const parsed = JSON.parse(stripJsonFence(content));
    const fields = normalizeCloudFields(parsed, effective);
    for (const key of ["summary", "root_cause", "recommended_action",
                       "risk", "evidence_refs"]) {
      if (!fields[key])
        throw new Error(`llm_missing_${key}`);
    }
    return analysisEvent(fields);
  } catch (error) {
    if (!effective.offlineFallback)
      throw error;
    return analysisEvent(fallbackFields(entries, effective.providerName,
                                        "cloud_error"));
  }
}
