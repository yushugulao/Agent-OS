# project61-agentOS-happylegend: plain uCore research platform branch

This branch is the plain-kernel baseline for the research Agent platform.

The kernel source under `os/`, the file-system builder under `nfs/`, and the boot/init helper under `scripts/` are restored from the upstream uCore 2025S source. The research platform work in this branch is placed in ordinary user space.

## Purpose

The branch answers one specific question: how far the research Agent platform can run on an unchanged uCore kernel before the later Agent-OS enhanced kernel is used.

It is not the Agent-OS kernel-enhanced version. There are no Agent syscalls, Agent Context pages, kernel file metadata indexes, or kernel Agent event queues in `os/`.

## Current User Program

The first native uCore entries are:

```text
user/src/research_platform_ucore_plain.c
user/src/research_platform_orchestrator.c
user/src/rp_planner.c
user/src/rp_retriever.c
user/src/rp_analyst.c
user/src/rp_reviewer.c
user/src/rp_writer.c
user/src/rp_repair.c
user/src/rp_auditor.c
```

`research_platform_ucore_plain` is a normal uCore user process. It embeds the current pure user-space research platform catalog and validates:

- 500 platform object counters.
- 120 service names.
- 28 feature groups.
- 13 platform self-check groups.
- 6 reference research platforms: Galaxy, AiiDA, DVC, MLflow, Nextflow, Snakemake.
- 6 mature capability mappings with a target coverage ratio of at least 30%.
- A plain user-space research run simulation with planning, literature, analysis, review, writing, repair, and audit roles.
- Local catalog search for workflow, Agent, evidence, provenance, and LLM related platform objects.

`research_platform_orchestrator` runs seven role programs as separate uCore user processes:

- planner,
- retriever,
- analyst,
- reviewer,
- writer,
- repair,
- auditor.

It uses ordinary `fork`, `exec`, and `waitpid`. This provides a plain-kernel baseline for the later Agent-OS multi-Agent version.

The program prints:

```text
research_platform_ucore_plain: passed
```

when the built-in checks pass.

## Build And Run

In WSL Ubuntu:

```bash
cd /mnt/e/计算机操作系统能力竞赛/project61-agentOS-happylegend-uCore
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=research_platform_ucore_plain
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=research_platform_ucore_plain
make build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=research_platform_orchestrator
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform LOG=warn INIT_PROC=research_platform_orchestrator
```

Expected key output:

```text
research_platform_ucore_plain summary
objects=500 object_total=102790 services=120 features=28 feature_units=299 checks=13 references=6 mappings=6
catalog_ok=1 checks_ok=1 mature_ok=1 run_ok=1 search_ok=1
research_platform_ucore_plain: passed
```

Expected orchestrator output:

```text
research_platform_orchestrator: start roles=7
rp_planner: workflow=lab-gene-x run=RUN-042 assignments=7 status=planned
rp_retriever: literature=3 evidence_links=5 status=ready
rp_analyst: datasets=4 statistics=6 figures=3 status=ready
rp_reviewer: claims=8 protocol_checks=5 release_checks=4 status=accepted
rp_writer: sections=6 citations=9 response_items=3 status=packaged
rp_repair: failed_stage=align action=minimal_rerun status=recovered
rp_auditor: provenance=verified release=ready package=ready status=passed
research_platform_orchestrator: roles_ok=7 roles_total=7
research_platform_orchestrator: passed
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

The current native program proves that the plain uCore kernel can boot and run a research-platform-shaped user process with the same catalog scale as the pure Python platform. Further migration work should move more behavior from embedded tables into active user-space services:

- Persistent platform state files in the uCore root file system.
- Expand the planner, retriever, analyst, reviewer, writer, repair, and auditor programs beyond status output.
- A user-space message protocol using only unchanged uCore syscalls.
- A host LLM gateway bridge exposed as ordinary input/output files or console packets.
- More executable checks for workflow portability, evidence tracing, release review, and AgentCompare comparison.

The later Agent-OS enhanced kernel version should use the same object names, role names, and output contracts so both kernels can run the same demonstration scenario.
