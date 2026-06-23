// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {generateLlmAnalysis, loadGatewayConfig} from "./llm.mjs";
import {parseAgentOsLog} from "./parser.mjs";

const entries = parseAgentOsLog(readFileSync("host/fixtures/labdemo.log", "utf8"));

const fallback = await generateLlmAnalysis(entries, {
  apiKey: "",
  providerName: "test-provider",
});
assert.equal(fallback.type, "LLM_ANALYSIS");
assert.equal(fallback.fields.mode, "fallback");
assert.equal(fallback.fields.reason, "missing_api_key");
assert.match(fallback.fields.root_cause, /memory limit exceeded/);
assert.match(fallback.fields.evidence_refs, /lab_RUN042_align_err/);

const failedCloud = await generateLlmAnalysis(entries, {
  apiKey: "bad-key",
  apiBaseUrl: "https://example.invalid/v1",
  providerName: "bad-provider",
  offlineFallback: true,
}, async () => {
  throw new Error("network down");
});
assert.equal(failedCloud.fields.mode, "fallback");
assert.equal(failedCloud.fields.reason, "cloud_error");

const badJson = await generateLlmAnalysis(entries, {
  apiKey: "bad-json-key",
  apiBaseUrl: "https://mock/v1",
  providerName: "json-provider",
  offlineFallback: true,
}, async () => ({
  ok: true,
  async json() {
    return {choices: [{message: {content: "not json"}}]};
  },
}));
assert.equal(badJson.fields.mode, "fallback");
assert.equal(badJson.fields.reason, "cloud_error");

const cloud = await generateLlmAnalysis(entries, {
  apiKey: "ok-key",
  apiBaseUrl: "https://mock/v1",
  model: "mock-model",
  providerName: "mock-provider",
}, async (url, request) => {
  assert.equal(url, "https://mock/v1/chat/completions");
  const body = JSON.parse(request.body);
  assert.equal(body.model, "mock-model");
  assert.ok(body.messages[1].content.includes("RUN-042"));
  return {
    ok: true,
    async json() {
      return {
        choices: [{
          message: {
            content: JSON.stringify({
              summary: "RUN-042 recovered",
              root_cause: "align memory limit",
              recommended_action: "rerun affected stages only",
              risk: "low",
              evidence_refs: "incident,query_file,report",
            }),
          },
        }],
      };
    },
  };
});
assert.equal(cloud.fields.mode, "cloud");
assert.equal(cloud.fields.provider, "mock-provider");
assert.equal(cloud.fields.summary, "RUN-042 recovered");

const config = loadGatewayConfig({
  LLM_API_BASE_URL: "https://api.deepseek.com/v1/",
  LLM_MODEL: "deepseek-chat",
  LLM_PROVIDER_NAME: "deepseek",
  LLM_OFFLINE_FALLBACK: "0",
  LLM_API_KEY: "x",
}, "__missing_env_file__");
assert.equal(config.apiBaseUrl, "https://api.deepseek.com/v1");
assert.equal(config.model, "deepseek-chat");
assert.equal(config.providerName, "deepseek");
assert.equal(config.offlineFallback, false);

console.log("host:test llm=passed fallback=1 cloud=1 bad_json=1");
