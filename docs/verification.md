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
rp_writer
rp_repair
rp_auditor
rp_query
rp_evidence
rp_llm_bridge
rp_privacy
rp_package
rp_release
rp_dossier
rp_metrics
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
rp_orch: start programs=22
rp_catalog: objects=500 services=120 features=28 status=ready
rp_object_store: records=8 status=ready
rp_object_query: hits=8 ready_hits=7 status=ready
rp_lineage: edges=7 status=ready
rp_site_export: pages=6 status=ready
rp_planner: workflow=lab-gene-x run=RUN-042 assignments=7 messages=14 schedule=ready status=planned
rp_retriever: literature=3 evidence_links=5 status=ready
rp_analyst: datasets=4 statistics=6 figures=3 schema_fields=17 replay=ready status=ready
rp_reviewer: claims=8 protocol_checks=5 release_checks=4 status=accepted
rp_lab: samples=4 quality_checks=7 protocol_checks=5 trials=4 labops=ready status=ready
rp_writer: sections=8 citations=9 response_items=3 status=packaged
rp_repair: failed_stage=align action=minimal_rerun attempts=2 status=recovered
rp_auditor: provenance=verified release=ready package=ready status=passed
rp_query: workflow=34 agent=26 evidence=10 status=ready
rp_evidence: claims=8 links=5 provenance=12 knowledge=4 status=ready
rp_llm_bridge: requests=1 responses=1 routes=3 relay=ready mode=template status=ready
rp_privacy: checked=5 redactions=0 status=ready
rp_package: artifacts=12 checks=19 fair=passed repro=ready status=ready
rp_release: decision=release checks=5 status=ready
rp_dossier: sections=14 review_board=accepted submit=ready status=ready
rp_metrics: telemetry_spans=8 acks=14 tools=23 sched=14 retry=2 relay=ready scanned=128 status=ready
rp_compare_plain: plain_kernel=passed objects=500 programs=22 state_files=48 acks=15 tools=24 status=ready
rp_orch: programs_ok=22 programs_total=22
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
- multi-process execution with twenty-two ordinary uCore user programs.
- ordinary file-backed state exchange across role programs.
- object catalog, reusable object records, object query, lineage, site export, task messages, role acknowledgements, tool logs, scheduling records, retry records, data dictionary, calculation replay, samples, quality, protocol, SOP, experiment, lab operations, training, telemetry, evidence, knowledge, LLM packet, host relay description, prompt routing, LLM audit log, privacy, FAIR data release, reproduction package, package, release, dossier, review governance, submission package, AgentCompare metrics, and plain-kernel comparison files.

It does not use Agent-OS kernel features. That is intentional for this plain-kernel baseline.
