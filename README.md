# project61-agentOS-happylegend: plain uCore research platform branch

This branch is the plain-kernel baseline for the research Agent platform.

The kernel source under `os/`, the file-system builder under `nfs/`, and the boot/init helper under `scripts/` are restored from the upstream uCore 2025S source. The research platform work in this branch is placed in ordinary user space.

## Purpose

The branch answers one specific question: how far the research Agent platform can run on an unchanged uCore kernel before the later Agent-OS enhanced kernel is used.

It is not the Agent-OS kernel-enhanced version. There are no Agent syscalls, Agent Context pages, kernel file metadata indexes, or kernel Agent event queues in `os/`.

## Current User Program

The first native uCore entries are:

```text
user/src/rp_plain.c
user/src/rp_orch.c
user/src/rp_catalog.c
user/src/rp_object_store.c
user/src/rp_object_query.c
user/src/rp_lineage.c
user/src/rp_site_export.c
user/src/rp_planner.c
user/src/rp_retriever.c
user/src/rp_analyst.c
user/src/rp_reviewer.c
user/src/rp_lab.c
user/src/rp_writer.c
user/src/rp_repair.c
user/src/rp_auditor.c
user/src/rp_query.c
user/src/rp_evidence.c
user/src/rp_llm_bridge.c
user/src/rp_privacy.c
user/src/rp_package.c
user/src/rp_release.c
user/src/rp_dossier.c
user/src/rp_metrics.c
user/src/rp_compare_plain.c
```

`rp_plain` is a normal uCore user process. It embeds the current pure user-space research platform catalog and validates:

- 500 platform object counters.
- 120 service names.
- 28 feature groups.
- 13 platform self-check groups.
- 6 reference research platforms: Galaxy, AiiDA, DVC, MLflow, Nextflow, Snakemake.
- 6 mature capability mappings with a target coverage ratio of at least 30%.
- A plain user-space research run simulation with planning, literature, analysis, review, writing, repair, and audit roles.
- Local catalog search for workflow, Agent, evidence, provenance, and LLM related platform objects.

`rp_orch` runs twenty-two platform programs as separate uCore user processes:

- catalog,
- object store,
- object query,
- lineage,
- site export,
- planner,
- retriever,
- analyst,
- reviewer,
- lab evidence service,
- writer,
- repair,
- auditor,
- query,
- evidence,
- LLM bridge,
- privacy review,
- package,
- release decision,
- final dossier,
- metrics service,
- plain-kernel comparison.

It uses ordinary `fork`, `exec`, and `waitpid`. This provides a plain-kernel baseline for the later Agent-OS multi-Agent version.

The role programs also exchange state through ordinary root-file-system files:

- `rp_plan`
- `rp_mail`
- `rp_ack`
- `rp_tool`
- `rp_sched`
- `rp_taskrec`
- `rp_budget`
- `rp_wfio`
- `rp_retryq`
- `rp_lit`
- `rp_data`
- `rp_datadic`
- `rp_compute`
- `rp_fail`
- `rp_samples`
- `rp_quality`
- `rp_review`
- `rp_review2`
- `rp_protocol`
- `rp_soplog`
- `rp_exper`
- `rp_labops`
- `rp_training`
- `rp_report`
- `rp_revision`
- `rp_fix`
- `rp_retrylog`
- `rp_telemetry`
- `rp_health`
- `rp_audit`
- `rp_status`
- `rp_objects`
- `rp_services`
- `rp_object_records`
- `rp_object_query`
- `rp_lineage`
- `rp_site`
- `rp_query`
- `rp_rank`
- `rp_runview`
- `rp_evidence`
- `rp_knowledge`
- `rp_llm_req`
- `rp_llmq`
- `rp_llm_resp`
- `rp_relay`
- `rp_prompt`
- `rp_llmlog`
- `rp_llmeval`
- `rp_privacy`
- `rp_package`
- `rp_datarel`
- `rp_dataver`
- `rp_repro`
- `rp_release`
- `rp_dossier`
- `rp_reviewops`
- `rp_submit`
- `rp_agentcmp`
- `rp_compare`

Each program validates the files it depends on before writing its own artifact. The orchestrator reads `rp_status`, `rp_audit`, and `rp_compare` after all children exit, then prints `state_ok=1`.

The program prints:

```text
rp_plain: passed
```

when the built-in checks pass.

## Build And Run

In WSL Ubuntu:

```bash
cd /mnt/e/计算机操作系统能力竞赛/project61-agentOS-happylegend-uCore
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_plain
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_plain
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch
```

Expected key output:

```text
rp_plain summary
objects=500 object_total=102790 services=120 features=28 feature_units=299 checks=13 references=6 mappings=6
catalog_ok=1 checks_ok=1 mature_ok=1 run_ok=1 search_ok=1
rp_plain: passed
```

Expected orchestrator output:

```text
rp_orch: start programs=22
rp_catalog: objects=500 services=120 features=28 status=ready
rp_object_store: records=8 status=ready
rp_object_query: hits=8 ready_hits=7 status=ready
rp_lineage: edges=7 status=ready
rp_site_export: pages=6 status=ready
rp_planner: workflow=lab-gene-x run=RUN-042 assignments=7 messages=14 schedule=ready status=planned
rp_retriever: literature=3 evidence_links=5 status=ready
rp_analyst: datasets=4 statistics=6 figures=3 schema_fields=17 failure=tool_output_missing status=ready
rp_reviewer: claims=8 protocol_checks=5 release_checks=4 rounds=2 status=accepted
rp_lab: samples=4 quality_checks=7 protocol_checks=5 trials=4 labops=ready status=ready
rp_writer: sections=8 citations=9 revisions=3 status=packaged
rp_repair: failed_stage=align action=minimal_rerun attempts=2 status=recovered
rp_auditor: provenance=verified release=ready package=ready status=passed
rp_query: workflow=34 agent=26 evidence=10 ranked=14 selected=5 status=ready
rp_evidence: claims=8 links=5 provenance=12 knowledge=4 status=ready
rp_llm_bridge: requests=3 responses=3 routes=4 eval=7 relay=ready status=ready
rp_privacy: checked=7 packets=3 redactions=0 status=ready
rp_package: artifacts=12 checks=21 fair=passed repro=ready status=ready
rp_release: decision=release checks=7 status=ready
rp_dossier: sections=20 review_board=accepted submit=ready status=ready
rp_metrics: telemetry_spans=8 acks=14 tools=36 sched=14 llm=3 eval=7 status=ready
rp_compare_plain: plain_kernel=passed objects=500 programs=22 state_files=60 acks=15 tools=38 status=ready
rp_orch: programs_ok=22 programs_total=22
rp_orch: state_ok=1
rp_orch: passed
```

The current upstream uCore kernel prints `all app are over!` after the init user program exits. In this branch that message means the plain user program finished and the kernel reached its existing no-more-apps path.

## Kernel Source Check

Use these checks to verify that this branch keeps the kernel source unchanged:

```bash
diff -qr ../_upstream_ucore_2025S/os ./os
diff -qr ../_upstream_ucore_2025S/nfs ./nfs
diff -qr ../_upstream_ucore_2025S/scripts ./scripts
```

No output means the directories match.

## Next Work

The current native programs prove that the plain uCore kernel can boot and run a research-platform-shaped catalog process plus a multi-process workflow with ordinary file-backed object storage, task messages, role acknowledgements, tool logs, scheduling records, task records, workflow import/export description, resource budget, failure classification, retry records, query, task ranking, run view, lineage, site export, data dictionary, calculation replay, samples, quality, protocol, SOP, experiment, lab operations, personnel training, telemetry, health summary, evidence, knowledge, semantic summary, systematic review summary, multi-round review, report revision package, LLM packet queue, host relay description, prompt routing, LLM audit log, LLM evaluation, privacy review, FAIR data release, data product summary, data product versioning, reproduction package, release, dossier, review governance, submission package, AgentCompare metrics, and comparison services. Further migration work should move more behavior from embedded tables into active user-space services:

- Persistent platform state files in the uCore root file system.
- Expand the planner, retriever, analyst, reviewer, writer, repair, auditor, object query, lineage, export, scheduling, task ranking, workflow import/export, resource budget, failure classification, retry handling, run views, data dictionary, calculation replay, sample, quality, protocol, SOP, experiment, lab operations, telemetry, health summaries, evidence, knowledge, FAIR data release, data product versioning, reproduction, review governance, LLM packet queue, host relay description, prompt routing, LLM evaluation, privacy, release, dossier, submission, and AgentCompare programs beyond the current fixed records.
- A richer user-space coordination protocol using only unchanged uCore syscalls.
- A host LLM relay that consumes the existing ordinary request files and writes ordinary response files.
- More executable checks for workflow portability, release review, and AgentCompare comparison.

The later Agent-OS enhanced kernel version should use the same object names, role names, and output contracts so both kernels can run the same demonstration scenario.
