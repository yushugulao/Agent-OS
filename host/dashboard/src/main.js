// SPDX-License-Identifier: Apache-2.0

import "./styles.css";

const gatewayUrl = import.meta.env.VITE_GATEWAY_URL || "http://127.0.0.1:8787";
const roles = ["orchestrator", "sentinel", "investigator", "recovery"];
const roleNames = {
  orchestrator: "编排 Agent",
  sentinel: "哨兵 Agent",
  investigator: "调查 Agent",
  recovery: "恢复 Agent",
};

const statusNames = {
  CREATED: "已创建",
  RUNNING: "运行中",
  WAITING: "等待中",
  PENDING: "待回放",
  RECOVERED: "已恢复",
  UNKNOWN: "未知",
  offline: "离线",
  online: "在线",
  ready: "就绪",
  streaming: "流式回放",
  pending: "待生成",
  cloud: "云端",
  fallback: "离线兜底",
  replay: "回放",
  live: "实时",
  running: "运行中",
  done: "完成",
  timeout: "超时",
  error: "错误",
};

const keyTimelineTypes = new Set([
  "INCIDENT_CREATED",
  "TOOL_CALL",
  "MESSAGE",
  "AUDIT",
  "ACTION",
  "REPORT",
  "FINAL",
  "LLM_ANALYSIS",
]);

const collaborationTypes = new Set([
  "INCIDENT_CREATED",
  "TOOL_CALL",
  "MESSAGE",
  "AUDIT",
  "ACTION",
  "CONTEXT_SNAPSHOT",
  "REPORT",
  "FINAL",
]);

const state = {
  connected: false,
  streaming: false,
  sourceMode: "replay",
  sourceStatus: "ready",
  lab: {},
  agents: {},
  events: [],
  benches: [],
  llm: null,
  report: null,
  finalStatus: "PENDING",
  lastError: "",
};
let currentEventSource = null;

for (const role of roles) {
  state.agents[role] = {
    role,
    pid: "-",
    state: "offline",
    context: "-",
    last: "等待回放数据",
  };
}

function roleName(role) {
  return roleNames[role] || role || "Agent";
}

function displayStatus(value) {
  return statusNames[value] || value || "-";
}

function shortText(value, limit = 46) {
  const text = String(value || "-");
  if (text.length <= limit)
    return text;
  return `${text.slice(0, limit - 1)}…`;
}

function lastEvent(type) {
  for (let index = state.events.length - 1; index >= 0; index--) {
    if (state.events[index].type === type)
      return state.events[index];
  }
  return null;
}

function hasEvent(match) {
  return state.events.some(match);
}

function statusClass(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("recover") || value.includes("ok") ||
      value.includes("allow") || value.includes("cloud"))
    return "good";
  if (value.includes("denied") || value.includes("duplicate") ||
      value.includes("fallback") || value.includes("oom") ||
      value.includes("limit"))
    return "warn";
  return "neutral";
}

function eventLabel(event) {
  const fields = event.fields || {};
  if (event.type === "INCIDENT_CREATED")
    return `故障注入：${fields.stage || "stage"} / ${fields.reason || "unknown"}`;
  if (event.type === "TOOL_CALL")
    return `${roleName(fields.role)} 调用 ${fields.tool || "tool"}`;
  if (event.type === "MESSAGE")
    return `${roleName(fields.from)} 发送消息给 ${roleName(fields.to)}`;
  if (event.type === "AUDIT")
    return `${roleName(fields.role)} 审计 ${fields.action || "audit"}：${fields.result || ""}`;
  if (event.type === "ACTION")
    return `${roleName(fields.role)} 重跑 ${fields.stage || "stage"} 阶段`;
  if (event.type === "CONTEXT_SNAPSHOT")
    return `${roleName(fields.role)} 快照 ${fields.records || 0} 条上下文`;
  if (event.type === "BENCH")
    return `${fields.case || "bench"} 指标 ${fields.ops || fields.attempts || ""}`;
  if (event.type === "REPORT")
    return `恢复报告 ${fields.artifact || ""}`;
  if (event.type === "FINAL")
    return `最终状态 ${fields.status || "unknown"}`;
  if (event.type === "LLM_ANALYSIS")
    return `LLM 分析来源：${displayStatus(fields.mode || "unknown")}`;
  return event.type;
}

function eventTone(event) {
  const fields = event.fields || {};
  if (event.type === "FINAL" || fields.status === "OK" || fields.result === "ALLOW")
    return "good";
  if (fields.result === "DENIED" || fields.result === "DUPLICATE" ||
      fields.reason === "memory_limit")
    return "warn";
  return "neutral";
}

function applyEvent(event) {
  const fields = event.fields || {};

  if (event.type === "LAB_INIT")
    state.lab = {...fields};

  if (event.type === "AGENT_CREATED" && fields.role) {
    state.agents[fields.role] = {
      ...(state.agents[fields.role] || {role: fields.role}),
      pid: fields.pid,
      context: fields.context,
      state: "CREATED",
      last: "进程已创建",
    };
  }

  if (event.type === "AGENT_STATE" && fields.role) {
    state.agents[fields.role] = {
      ...(state.agents[fields.role] || {role: fields.role}),
      state: fields.state || "RUNNING",
      last: fields.payload || displayStatus(fields.state) || "状态已更新",
    };
  }

  if (event.type === "TOOL_CALL" && fields.role) {
    state.agents[fields.role] = {
      ...(state.agents[fields.role] || {role: fields.role}),
      last: `${fields.tool || "tool"} seq=${fields.seq || "-"}`,
    };
  }

  if (event.type === "AUDIT" && fields.role) {
    state.agents[fields.role] = {
      ...(state.agents[fields.role] || {role: fields.role}),
      last: `${fields.action || "audit"} ${fields.result || ""}`,
    };
  }

  if (event.type === "ACTION" && fields.role) {
    state.agents[fields.role] = {
      ...(state.agents[fields.role] || {role: fields.role}),
      state: "RUNNING",
      last: `${fields.stage || "stage"} ${fields.status || ""}`,
    };
  }

  if (event.type === "REPORT")
    state.report = fields;

  if (event.type === "FINAL")
    state.finalStatus = fields.status || "UNKNOWN";

  if (event.type === "BENCH")
    state.benches.push(fields);

  if (event.type === "LLM_ANALYSIS")
    state.llm = fields;

  if (event.type !== "BENCH")
    state.events.push(event);
}

function resetState() {
  state.events = [];
  state.benches = [];
  state.llm = null;
  state.report = null;
  state.finalStatus = "PENDING";
  state.lastError = "";
  state.sourceStatus = "ready";
  state.lab = {};
  for (const role of roles) {
    state.agents[role] = {
      role,
      pid: "-",
      state: "offline",
      context: "-",
      last: "等待回放数据",
    };
  }
}

function centerNode() {
  const final = lastEvent("FINAL");
  const incident = lastEvent("INCIDENT_CREATED");
  if (final) {
    return {
      label: "任务收束",
      title: displayStatus(final.fields?.status || state.finalStatus),
      detail: "恢复报告已生成，事件链路闭环",
      tone: statusClass(final.fields?.status || state.finalStatus),
    };
  }
  if (incident) {
    return {
      label: incident.fields?.id || "INCIDENT",
      title: incident.fields?.stage || "故障事件",
      detail: incident.fields?.reason || "等待调查",
      tone: statusClass(incident.fields?.reason),
    };
  }
  return {
    label: "等待事件",
    title: "Agent Loop",
    detail: "加载 replay 或连接 live 数据源",
    tone: "neutral",
  };
}

function linkClass(active, tone = "neutral") {
  return `topology-link ${active ? `is-active ${tone}` : ""}`;
}

function renderTopologyNode(role, position) {
  const agent = state.agents[role] || {role, pid: "-", state: "offline", last: "-"};
  return `
    <article class="topology-node ${position} ${role} ${statusClass(agent.state)}">
      <span class="node-kicker">${role}</span>
      <strong>${roleName(role)}</strong>
      <small>pid ${agent.pid} · ${displayStatus(agent.state)}</small>
      <p>${shortText(agent.last, 42)}</p>
    </article>
  `;
}

function renderCollaborationChips() {
  const shown = state.events
    .filter((event) => collaborationTypes.has(event.type))
    .slice(-5)
    .reverse();
  if (shown.length === 0)
    return `<li class="flow-chip"><strong class="muted">等待 Agent 协作事件。</strong></li>`;
  return shown.map((event) => `
    <li class="flow-chip ${eventTone(event)}">
      <span>${event.type}</span>
      <strong>${shortText(eventLabel(event), 58)}</strong>
    </li>
  `).join("");
}

function renderTopology() {
  const center = centerNode();
  const incidentToSentinel = hasEvent((event) =>
    event.type === "INCIDENT_CREATED" || event.type === "AGENT_STATE" &&
      event.fields?.role === "sentinel");
  const sentinelToCenter = hasEvent((event) =>
    (event.type === "TOOL_CALL" || event.type === "AUDIT") &&
      event.fields?.role === "sentinel");
  const sentinelToInvestigator = hasEvent((event) =>
    event.type === "MESSAGE" && event.fields?.from === "sentinel" &&
      event.fields?.to === "investigator");
  const investigatorToCenter = hasEvent((event) =>
    event.fields?.role === "investigator" &&
      (event.type === "TOOL_CALL" || event.type === "CONTEXT_SNAPSHOT"));
  const investigatorToRecovery = hasEvent((event) =>
    event.type === "MESSAGE" && event.fields?.from === "investigator" &&
      event.fields?.to === "recovery");
  const recoveryToCenter = hasEvent((event) =>
    event.fields?.role === "recovery" &&
      (event.type === "ACTION" || event.type === "AUDIT" ||
       event.type === "TOOL_CALL")) || state.finalStatus === "RECOVERED";

  return `
    <section class="panel topology-panel">
      <div class="panel-heading">
        <div>
          <p class="section-label">Agent Collaboration</p>
          <h2>Agent 协作拓扑</h2>
        </div>
        <span class="pill ${center.tone}">${center.title}</span>
      </div>
      <div class="topology-layout">
        <div class="topology-map">
          <svg class="topology-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <defs>
              <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
                <path d="M0,0 L8,4 L0,8 Z"></path>
              </marker>
            </defs>
            <path class="${linkClass(incidentToSentinel, "warn")}" d="M50 50 C40 40 34 29 24 24"></path>
            <path class="${linkClass(sentinelToCenter, "warn")}" d="M24 24 C30 40 38 47 50 50"></path>
            <path class="${linkClass(sentinelToInvestigator, "neutral")}" d="M32 20 C44 10 56 10 68 20"></path>
            <path class="${linkClass(investigatorToCenter, "neutral")}" d="M76 24 C68 36 62 45 50 50"></path>
            <path class="${linkClass(investigatorToRecovery, "neutral")}" d="M78 32 C90 44 90 58 78 70"></path>
            <path class="${linkClass(recoveryToCenter, "good")}" d="M76 76 C66 66 60 56 50 50"></path>
          </svg>
          ${renderTopologyNode("sentinel", "node-nw")}
          ${renderTopologyNode("investigator", "node-ne")}
          ${renderTopologyNode("orchestrator", "node-sw")}
          ${renderTopologyNode("recovery", "node-se")}
          <article class="incident-node ${center.tone}">
            <span>${center.label}</span>
            <strong>${center.title}</strong>
            <small>${center.detail}</small>
          </article>
        </div>
        <aside class="flow-stack">
          <span class="mini-title">最近协作</span>
          <ol>${renderCollaborationChips()}</ol>
        </aside>
      </div>
    </section>
  `;
}

function renderAgent(agent) {
  return `
    <article class="agent-card ${agent.role}">
      <div class="agent-card__top">
        <div>
          <span class="role">${agent.role}</span>
          <strong>${roleName(agent.role)}</strong>
        </div>
        <span class="pill ${statusClass(agent.state)}">${displayStatus(agent.state)}</span>
      </div>
      <dl>
        <div><dt>进程号</dt><dd>${agent.pid}</dd></div>
        <div><dt>上下文区</dt><dd>${agent.context}</dd></div>
        <div><dt>最新动作</dt><dd>${shortText(agent.last, 54)}</dd></div>
      </dl>
    </article>
  `;
}

function renderTimeline() {
  const shown = state.events
    .filter((event) => keyTimelineTypes.has(event.type))
    .slice(-10)
    .reverse();
  if (shown.length === 0)
    return `<li class="timeline-empty">等待关键事件。</li>`;
  return shown.map((event) => `
    <li class="timeline-item ${eventTone(event)}">
      <span class="timeline-type">${event.type}</span>
      <span class="timeline-text">${shortText(eventLabel(event), 72)}</span>
      <span class="timeline-line">L${event.line}</span>
    </li>
  `).join("");
}

function renderBench() {
  if (state.benches.length === 0)
    return `<p class="muted">等待回放中的 labbench 指标。</p>`;
  return state.benches.map((bench) => `
    <div class="metric">
      <span>${bench.case}</span>
      <strong>${bench.ops_per_tick || bench.executed || bench.ops || "-"}</strong>
      <small>${bench.speedup_x100 ? `速度比x100=${bench.speedup_x100}` : `ticks=${bench.ticks}`}</small>
    </div>
  `).join("");
}

function render() {
  const app = document.querySelector("#app");
  const llm = state.llm || {};
  const report = state.report || {};
  const project = state.lab.project || "lab-gene-x";
  const runId = state.lab.run_id || "RUN-042";
  const workflow = state.lab.workflow || "nightly-regression";

  app.innerHTML = `
    <main class="shell">
      <header class="topbar">
        <section class="hero-copy">
          <p class="eyebrow">Agent-OS 宿主机可视化大屏</p>
          <h1>${project} / ${runId}</h1>
          <p>${workflow} 故障恢复流程，数据来自内核 Agent 结构化事件。</p>
        </section>
        <section class="status-strip">
          <div>
            <span>数据源</span>
            <strong>${displayStatus(state.sourceMode)}</strong>
          </div>
          <div>
            <span>网关</span>
            <strong>${state.connected ? "在线" : "离线"}</strong>
          </div>
          <div>
            <span>事件流</span>
            <strong>${state.streaming ? "流式回放" : displayStatus(state.sourceStatus)}</strong>
          </div>
          <div>
            <span>LLM</span>
            <strong>${displayStatus(llm.mode || "pending")}</strong>
          </div>
          <div>
            <span>最终状态</span>
            <strong class="${statusClass(state.finalStatus)}">${displayStatus(state.finalStatus)}</strong>
          </div>
        </section>
        <nav class="actions">
          <button id="loadReplay" type="button">加载回放</button>
          <button id="streamReplay" type="button">流式播放事件</button>
        </nav>
      </header>

      ${state.lastError ? `<p class="error">${state.lastError}</p>` : ""}

      <section class="hero-grid">
        ${renderTopology()}
        <section class="panel insight-panel">
          <div class="panel-heading">
            <div>
              <p class="section-label">LLM Insight</p>
              <h2>LLM 分析</h2>
            </div>
            <span class="pill ${statusClass(llm.mode)}">${displayStatus(llm.mode || "pending")}</span>
          </div>
          <p class="lead">${llm.summary || "等待 LLM_ANALYSIS 事件。"}</p>
          <dl class="insight-list">
            <div><dt>根因</dt><dd>${llm.root_cause || "-"}</dd></div>
            <div><dt>建议动作</dt><dd>${llm.recommended_action || "-"}</dd></div>
            <div><dt>风险</dt><dd>${llm.risk || "-"}</dd></div>
            <div><dt>证据引用</dt><dd>${llm.evidence_refs || "-"}</dd></div>
          </dl>
        </section>
      </section>

      <section class="agents-grid">
        ${roles.map((role) => renderAgent(state.agents[role])).join("")}
      </section>

      <section class="content-grid">
        <section class="panel timeline-panel">
          <div class="panel-heading">
            <div>
              <p class="section-label">Event Stream</p>
              <h2>关键事件流</h2>
            </div>
            <span>${state.events.length} 条事件</span>
          </div>
          <ol class="timeline">${renderTimeline()}</ol>
        </section>

        <section class="panel">
          <div class="panel-heading">
            <div>
              <p class="section-label">Recovery</p>
              <h2>恢复报告</h2>
            </div>
            <span class="pill ${statusClass(report.status)}">${displayStatus(report.status || "pending")}</span>
          </div>
          <dl class="insight-list">
            <div><dt>工件</dt><dd>${report.artifact || "-"}</dd></div>
            <div><dt>引用</dt><dd>${report.refs || "-"}</dd></div>
            <div><dt>序号</dt><dd>${report.seq || "-"}</dd></div>
          </dl>
        </section>

        <section class="panel metrics-panel">
          <div class="panel-heading">
            <div>
              <p class="section-label">Bench Signals</p>
              <h2>性能指标</h2>
            </div>
            <span>${state.benches.length} 项指标</span>
          </div>
          <div class="metrics">${renderBench()}</div>
        </section>
      </section>
    </main>
  `;

  document.querySelector("#loadReplay").addEventListener("click", loadReplay);
  document.querySelector("#streamReplay").addEventListener("click", streamReplay);
}

async function loadReplay() {
  resetState();
  render();
  try {
    const response = await fetch(`${gatewayUrl}/api/replay`);
    if (!response.ok)
      throw new Error(`gateway ${response.status}`);
    const data = await response.json();
    state.connected = true;
    state.sourceMode = data.mode || "replay";
    state.sourceStatus = data.summary?.status || "ready";
    for (const event of data.events)
      applyEvent(event);
    const shouldAutoStream = data.mode === "live" &&
      data.summary?.status !== "done";
    if (shouldAutoStream) {
      render();
      streamReplay();
      return;
    }
  } catch (error) {
    state.connected = false;
    state.lastError = `无法从 ${gatewayUrl} 加载回放：${error.message}`;
  }
  render();
}

function streamReplay() {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
  resetState();
  state.streaming = true;
  render();

  const source = new EventSource(`${gatewayUrl}/events?interval=220`);
  currentEventSource = source;
  source.onopen = () => {
    state.connected = true;
    state.sourceStatus = "streaming";
    render();
  };
  source.onmessage = (message) => {
    applyEvent(JSON.parse(message.data));
    render();
  };
  source.addEventListener("done", () => {
    state.streaming = false;
    currentEventSource = null;
    source.close();
    render();
  });
  source.onerror = () => {
    state.connected = false;
    state.streaming = false;
    state.lastError = `${gatewayUrl} 的事件流已停止。`;
    currentEventSource = null;
    source.close();
    render();
  };
}

render();
loadReplay();
