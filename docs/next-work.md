# Next Work For Plain uCore Migration

The current branch has a working native baseline. It now includes active cross-file consistency checks, concrete RUN-042 artifact files, workflow runner execution files, Agent collaboration records, file-backed Host LLM Relay protocol files, host UI export files, and a 64-check user-space test suite over ordinary uCore state files. The remaining work is to keep moving behavior from compact records into active user-space services without changing the kernel.

## Required Direction

The plain uCore platform should keep the same object names and output contract as the Python platform and the future Agent-OS platform. New work should add behavior in user space, not kernel hooks.

The host-side platform is being upgraded from metadata-heavy records to a real research Agent application. The plain uCore migration must track that higher target. It may expose limitations of an unchanged kernel, but it should not remove the same workflow concepts.

## Host Parity Targets

The plain uCore version should preserve these host-platform capabilities in a form that can run on an unchanged uCore kernel:

- Web UI data: uCore does not need to serve HTTP directly, but it should produce ordinary files for a host UI home page, run detail view, Agent detail view, evidence detail view, and comparison metrics view.
- Real artifact operations: read ordinary input files, write intermediate artifacts, report text, logs, and chart data instead of only writing summary metadata.
- Minimal workflow runner: represent a stage DAG, dependencies, failure, retry, cache reuse, and per-stage logs as ordinary files.
- Host LLM relay: keep a request queue and response files that support template mode without a key and live cloud mode on the host when a key exists; no secret should be stored inside the uCore image.
- Agent collaboration: keep orchestrator, retriever, analyst, reviewer, writer, recovery, and auditor messages plus decision records as executable user-space artifacts.
- AgentCompare: preserve explicit plain-kernel pain points: many file scans, convention-based state, user-space-only permissions, untrusted context, expensive path reconstruction, and multi-step failure recovery.
- Test growth: keep increasing small tests or check programs around core services, exported UI data, demo run artifacts, workflow runner execution files, Agent collaboration records, LLM relay records, and AgentCompare records. The current uCore branch already runs a 64-check user-space suite.
- Real run evidence: every major output should be tied to a concrete input, generated artifact, observed failure, recovery action, report section, or comparison result.

## Planned Increments

1. Expand the current reusable object collections into larger workflow, workflow invocation, workflow completion, run configuration, backend scenario, evidence, claim-record, provenance-path, knowledge, dataset, data-profile, figure, report, project-policy, compliance, risk, CAPA, release-delta, execution-plan, execution-observer, FAIR data release, data product version, reproduction, LLM packet queue, host relay, LLM evaluation, prompt routing, sample, protocol, experiment, trial, telemetry, health, and review records.
2. Expand catalog, query, object query, lineage, site export, evidence, claim-record, provenance-path, knowledge, package, data release, data version, release delta, reproduction, review governance, release, dossier, submission, compare, planner, retriever, analyst, data dictionary, data profile, figure record, calculation replay, sample, quality, protocol, SOP, experiment, trial record, lab operations, telemetry, health, reviewer, governance, writer, repair, auditor, scheduling, task ranking, workflow import/export, resource budget, project policy, risk register, CAPA records, run configuration, workflow invocation, workflow completion, execution observer, backend scenario, failure classification, run views, retry handling, LLM packet queue, host relay, LLM evaluation, prompt routing, privacy, compliance, and AgentCompare programs beyond the current fixed records.
3. Deepen the current file-backed task message, acknowledgement, scheduling, task-record, task-ranking, workflow import/export, run configuration, workflow invocation, workflow completion, execution plan, worker health, run timeline, budget, policy, compliance, risk, CAPA, release-delta, failure, retry, run-view, review-round, revision, data-version, and tool-log protocol with richer scheduling and review cases.
4. Continue moving selected checks from static counters into active operations over stored records. The current consistency checker already validates task, LLM, workflow invocation, completion, and backend scenario relations.
5. Add an AgentCompare plain-kernel runner that emits the same high-level result fields as the future Agent-OS runner.
6. Add a host-side LLM relay that consumes the prepared ordinary request queue and writes ordinary response files.
7. Continue expanding uCore user-space exports for UI pages and run details so the host web service can render the same scenario from plain-kernel output files. The current branch already exports home, run, Agent, evidence, and comparison page data.
8. Continue expanding concrete input, artifact, report, log, chart-data, and per-stage runner records for `RUN-042`. The current branch already writes a small FASTQ input, stage DAG, stage log, stage-state table, cache index, retry plan, event stream, artifact manifest, recovered align artifact, report text, chart data, and runner summary.

## Non-Goals For This Branch

- No Agent syscall implementation.
- No kernel Agent Context.
- No kernel file metadata service.
- No kernel Agent event queue.
- No kernel LLM networking.

Those belong to the enhanced kernel version.
