# Next Work For Plain uCore Migration

The current branch has a working native baseline. The remaining work is to turn the compact native catalog into an active user-space platform without changing the kernel.

## Required Direction

The plain uCore platform should keep the same object names and output contract as the Python platform and the future Agent-OS platform. New work should add behavior in user space, not kernel hooks.

## Planned Increments

1. Expand the current reusable object collections into larger workflow, workflow invocation, workflow completion, run configuration, backend scenario, evidence, claim-record, provenance-path, knowledge, dataset, data-profile, figure, report, project-policy, compliance, risk, CAPA, release-delta, execution-plan, execution-observer, FAIR data release, data product version, reproduction, LLM packet queue, host relay, LLM evaluation, prompt routing, sample, protocol, experiment, trial, telemetry, health, and review records.
2. Expand catalog, query, object query, lineage, site export, evidence, claim-record, provenance-path, knowledge, package, data release, data version, release delta, reproduction, review governance, release, dossier, submission, compare, planner, retriever, analyst, data dictionary, data profile, figure record, calculation replay, sample, quality, protocol, SOP, experiment, trial record, lab operations, telemetry, health, reviewer, governance, writer, repair, auditor, scheduling, task ranking, workflow import/export, resource budget, project policy, risk register, CAPA records, run configuration, workflow invocation, workflow completion, execution observer, backend scenario, failure classification, run views, retry handling, LLM packet queue, host relay, LLM evaluation, prompt routing, privacy, compliance, and AgentCompare programs beyond the current fixed records.
3. Deepen the current file-backed task message, acknowledgement, scheduling, task-record, task-ranking, workflow import/export, run configuration, workflow invocation, workflow completion, execution plan, worker health, run timeline, budget, policy, compliance, risk, CAPA, release-delta, failure, retry, run-view, review-round, revision, data-version, and tool-log protocol with richer scheduling and review cases.
4. Move selected checks from static counters into active operations over stored records.
5. Add an AgentCompare plain-kernel runner that emits the same high-level result fields as the future Agent-OS runner.
6. Add a host-side LLM relay that consumes the prepared ordinary request queue and writes ordinary response files.

## Non-Goals For This Branch

- No Agent syscall implementation.
- No kernel Agent Context.
- No kernel file metadata service.
- No kernel Agent event queue.
- No kernel LLM networking.

Those belong to the enhanced kernel version.
