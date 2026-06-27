# Next Work For Plain uCore Migration

The current branch has a working native baseline for the research Agent platform on an unchanged uCore kernel. It runs 62 platform programs, writes 221 compact state files, emits 268 tool-event records, publishes 32 Host Reader views, and exposes a 2120-check user-space test suite through `rp_tests` and `rp_agentcmp`.

The platform already covers the RUN-042 scenario with concrete input files, generated artifacts, report text, logs, chart data, workflow DAG records, retry records, cache records, Agent messages, review records, delivery records, project review records, Host LLM Relay packets, calculation job records, Palmer Penguins real-task validation records, experiment campaign records, statistical design records, model registry records, release dossier records, mature-platform mapping records, provenance views, and provenance query records.

## Required Direction

The plain uCore platform should keep the same object names and output contract as the Python host platform and the enhanced Agent-OS platform. New plain-target work should add behavior in user space, not kernel hooks.

The host-side platform is being upgraded from metadata-heavy records to a real research Agent application. The plain uCore migration must track that higher target. It may expose limitations of an unchanged kernel, but it should not remove the same workflow concepts.

## Host Parity Targets

- Web UI data: produce ordinary files for home, run detail, Agent detail, evidence detail, project, data, artifact, comparison, bio, lab resource, publication, knowledge, runtime, provenance, provenance query, route manifest, API payload, artifact preview, POST action, request validation, and side-effect views.
- Real artifact operations: read ordinary input files, write intermediate artifacts, report text, logs, chart data, and package outputs instead of only writing summary metadata.
- Data pipeline: preserve input scanning, dataset snapshots, previews, quality checks, transformations, and dataset collection output as ordinary files that the host UI and future Agent-OS version can consume.
- Workflow runner: represent a stage DAG, dependencies, failure, retry, cache reuse, and per-stage logs as ordinary files.
- Host LLM Relay: keep a request queue, route table, packet schema checks, guard checks, fallback decisions, response files, quality records, and a reader page that support template mode without a key and live cloud mode on the host when a key exists. No secret should be stored inside the uCore image.
- Agent collaboration: keep orchestrator, retriever, analyst, reviewer, writer, recovery, and auditor messages plus decision records as executable user-space artifacts.
- Provenance services: keep `rp_prov_view` for timeline views, provenance edges, and evidence packets; keep `rp_prov_query` for saved graph queries, reusable templates, executions, comparisons, exports, and reviewer packets.
- AgentCompare: preserve explicit plain-kernel pain points: many file scans, convention-based state, user-space-only permissions, untrusted context, expensive path reconstruction, and multi-step failure recovery.
- Test growth: keep increasing small checks around core services, exported UI data, Host Web/API payloads, action records, workflow records, Agent collaboration records, LLM relay records, provenance records, and AgentCompare records.
- Real run evidence: every major output should be tied to a concrete input, generated artifact, observed failure, recovery action, report section, or comparison result.

## Planned Increments

1. Expand reusable object collections into larger workflow, evidence, claim-record, provenance, knowledge, dataset, figure, report, project-policy, compliance, risk, CAPA, release, reproduction, LLM packet, sample, protocol, telemetry, health, and review records.
2. Expand the platform programs beyond fixed summaries by performing more active reads over stored records.
3. Deepen task message, acknowledgement, scheduling, task-ranking, workflow import/export, run configuration, execution plan, worker health, budget, policy, failure, retry, review, revision, data-version, and tool-log records.
4. Continue moving selected checks from static counters into active operations over stored files.
5. Add an AgentCompare plain-kernel runner that emits the same high-level fields as the enhanced Agent-OS runner.
6. Expand the host-side relay process with more provider adapters, stricter response scoring, and route-specific validation over the prepared LLM request queue.
7. Continue expanding uCore user-space exports so the host web service can render the same scenario from plain-kernel output files.
8. Continue expanding concrete input, artifact, report, log, chart-data, data-pipeline, and per-stage runner records for `RUN-042`.

## Non-Goals For This Branch

- No Agent syscall implementation.
- No kernel Agent Context.
- No kernel file metadata service.
- No kernel Agent event queue.
- No kernel LLM networking.

Those belong to the enhanced kernel version.
