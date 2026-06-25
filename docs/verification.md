# Plain uCore Platform Verification

## Build

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_plain
```

Result:

```text
Build kernel done
```

The user image contains:

```text
usershell
rp_plain
rp_orch
rp_catalog
rp_object_store
rp_object_query
rp_lineage
rp_site_export
rp_planner
rp_retriever
rp_analyst
rp_reviewer
rp_lab
rp_governance
rp_writer
rp_repair
rp_auditor
rp_query
rp_evidence
rp_llm_bridge
rp_llm_relay
rp_privacy
rp_runconf
rp_execobs
rp_invoke
rp_complete
rp_artifact_ops
rp_workflow_runner
rp_agent_collab
rp_package
rp_delta
rp_release
rp_dossier
rp_backend
rp_consistency
rp_metrics
rp_ui_export
rp_test_suite
rp_compare_plain
```

## Run Catalog Program

```bash
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_plain
```

Observed key output:

```text
rp_plain summary
objects=500 object_total=102790 services=120 features=28 feature_units=299 checks=13 references=6 mappings=6
search workflow=34 agent=26 evidence=10 provenance=12 llm=11
reference platforms: Galaxy AiiDA DVC MLflow Nextflow Snakemake
catalog_ok=1 checks_ok=1 mature_ok=1 run_ok=1 search_ok=1
rp_plain: passed
```

The upstream kernel prints:

```text
all app are over!
```

after the init program exits. The platform result is taken from the `passed` line before that kernel termination path.

## Run Role Orchestrator

```bash
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=rp_orch
```

Observed key output:

```text
rp_orch: start programs=36
rp_catalog: objects=500 services=120 features=28 status=ready
rp_object_store: records=8 status=ready
rp_object_query: hits=8 ready_hits=7 status=ready
rp_lineage: edges=7 status=ready
rp_site_export: pages=6 status=ready
rp_planner: workflow=lab-gene-x run=RUN-042 assignments=7 messages=21 schedule=ready status=planned
rp_retriever: literature=3 evidence_links=5 status=ready
rp_analyst: datasets=4 profiles=4 statistics=6 figures=3 failure=tool_output_missing status=ready
rp_reviewer: claims=8 protocol_checks=5 release_checks=4 rounds=2 status=accepted
rp_lab: samples=4 quality_checks=7 protocol_checks=5 trials=4 trial_records=4 status=ready
rp_governance: risks=3 capa=2 deviations=1 status=ready
rp_writer: sections=8 citations=9 revisions=3 status=packaged
rp_repair: failed_stage=align action=minimal_rerun attempts=2 status=recovered
rp_auditor: provenance=verified release=ready package=ready status=passed
rp_query: workflow=34 agent=26 evidence=10 ranked=21 selected=10 status=ready
rp_evidence: claims=8 links=5 claim_records=8 paths=3 status=ready
rp_llm_bridge: requests=3 responses=3 routes=4 eval=7 relay=ready status=ready
rp_llm_relay: packets=3 routes=4 guard=ready fallback=1 status=ready
rp_privacy: checked=13 packets=3 redactions=0 compliance=accepted status=ready
rp_runconf: profiles=2 validations=2 drift=1 status=ready
rp_execobs: timeline=9 workers=4 controls=5 observer=ready status=ready
rp_invoke: steps=10 attempts=12 outputs=6 status=recovered
rp_complete: hooks=4 events=1 actions=4 status=ready
rp_artifact_ops: inputs=2 stages=5 retries=1 artifacts=4 status=ready
rp_workflow_runner: stages=5 events=8 retries=1 cache_hits=1 status=ready
rp_agent_collab: agents=7 messages=21 decisions=8 handoffs=6 status=ready
rp_package: artifacts=42 checks=63 fair=passed repro=ready status=ready
rp_delta: items=20 reviews=1 decision=accepted status=ready
rp_release: decision=release checks=17 status=ready
rp_dossier: sections=36 review_board=accepted submit=ready status=ready
rp_backend: cases=4 executable=2 exports=1 status=ready
rp_consistency: checks=50 tasks=21 llm=3 relay=5 workflow=5 backend=4 artifacts=4 agents=7 status=ready
rp_metrics: telemetry_spans=8 acks=27 tools=106 delta_items=20 status=ready
rp_ui_export: pages=5 run=RUN-042 compare=ready status=ready
rp_test_suite: tests=64 catalog=passed artifacts=passed workflow=passed collaboration=passed ui=passed llm=passed compare=passed status=passed
rp_compare_plain: plain_kernel=passed objects=500 programs=36 state_files=121 acks=29 tools=119 status=ready
rp_orch: programs_ok=36 programs_total=36
rp_orch: state_ok=1
rp_orch: passed
```

## Kernel Source Check

From the repository root:

```bash
diff -qr ../_upstream_ucore_2025S/os ./os
diff -qr ../_upstream_ucore_2025S/nfs ./nfs
diff -qr ../_upstream_ucore_2025S/scripts ./scripts
```

Expected result: no diff output.

## Current Coverage

This first native uCore version validates:

- catalog scale,
- service inventory,
- feature groups,
- mature reference platform mappings,
- platform self-check status,
- catalog search,
- a complete research run simulation with one failed stage repaired in user space.
- multi-process execution with thirty-six ordinary uCore user programs.
- ordinary file-backed state exchange across role programs.
- active cross-file consistency checks across tasks, LLM packets, workflow invocation, completion hooks, backend cases, and runner artifacts.
- user-space test suite with 64 checks over catalog, workflow, artifact operations, workflow runner files, Agent collaboration, UI export, LLM relay, AgentCompare, and consistency records.
- object catalog, reusable object records, object query, lineage, site export, task messages, role acknowledgements, tool logs, scheduling records, task records, task ranking, workflow import/export description, resource budget, project policy, risk register, CAPA records, release delta review, run configuration, workflow invocation, workflow completion, execution plan, worker health, execution timeline, observer evidence, concrete input files, stage DAG, stage logs, workflow runner execution state, cache records, retry plan, run events, artifact manifest, recovered artifacts, report text, chart data, UI export files, failure classification, retry records, run views, data dictionary, data profile records, figure records, calculation replay, samples, quality, protocol, SOP, experiment, trial records, lab operations, training, telemetry, health summaries, evidence, claim records, provenance paths, knowledge, multi-round review, report revision package, LLM packet queue, host relay description, prompt routing, LLM audit log, LLM evaluation, privacy, compliance record, FAIR data release, data product versioning, reproduction package, package, release, dossier, review governance, submission package, backend scenario evidence, AgentCompare metrics, and plain-kernel comparison files.

It does not use Agent-OS kernel features. That is intentional for this plain-kernel baseline.
