#ifndef AGENTNEXUS_SEED_H
#define AGENTNEXUS_SEED_H

/* Tracked ASCII source capsules materialized by the Nexus Guest at boot. */
#define AGENTNEXUS_SEED_VERSION 2U

#define AGENTNEXUS_SEED_PROJECT "lab-gene-x"
#define AGENTNEXUS_SEED_WORKFLOW "nightly-regression"
#define AGENTNEXUS_SEED_RUN_ID "RUN-042"

#define AGENTNEXUS_SEED_CASE_NAME "nexus_case"
#define AGENTNEXUS_SEED_MEAS_NAME "nexus_meas"
#define AGENTNEXUS_SEED_STATE_NAME "nexus_state"

#define AGENTNEXUS_SEED_CASE_BODY \
	"schema=agentos.nexus.case.v1\n" \
	"source_revision=base-96613ea\n" \
	"source_path=docs/agentos/scenario-script.md\n" \
	"source_lines=33-46\n" \
	"source_pipeline=prepare>align>analyze>report>archive\n" \
	"source_roles=Orchestrator,Sentinel,Investigator,Recovery\n" \
	"nexus_derived_project=lab-gene-x\n" \
	"nexus_derived_workflow=nightly-regression\n" \
	"nexus_derived_run_id=RUN-042\n" \
	"nexus_derived_incident=align_memory_limit\n" \
	"nexus_derived_coordination=watch_nonbusy>query>delegate>plan>govern>publish>audit\n" \
	"nexus_derived_required_roles=coordinator,system,research,analyst\n" \
	"nexus_derived_claim=tracked_scenario_capsule\n"

#define AGENTNEXUS_SEED_MEAS_BODY \
	"schema=agentos.nexus.measurement.v2\n" \
	"source_revision=2b14fb1f74b9bd093e6de939a16554620835699e\n" \
	"source_manifest=one_shot_metrics/data/20260811/manifest.json\n" \
	"source_table=one_shot_metrics/data/20260811/tables/contest_paired.csv\n" \
	"source_results=docs/contest/performance-results.md\n" \
	"source_results_lines=37-50\n" \
	"benchmark=file_query_core_path_paired\n" \
	"records=96\n" \
	"traversal_us=34712.5\n" \
	"indexed_us=13293.5\n" \
	"paired_ratio_median=3.118\n" \
	"wins=16/16\n" \
	"nexus_derived_checks=16/16\n" \
	"nexus_derived_checks_basis=wins\n" \
	"nexus_derived_claim=published_snapshot\n" \
	"nexus_derived_measurement_scope=historical_not_this_boot\n"

#define AGENTNEXUS_SEED_STATE_BODY \
	"schema=agentos.nexus.state.v1\n" \
	"source_revision=base-96613ea\n" \
	"nexus_derived_project=lab-gene-x\n" \
	"nexus_derived_workflow=nightly-regression\n" \
	"nexus_derived_run_id=RUN-042\n" \
	"source=agentnexus_ucore\n" \
	"claim=this_boot_runtime_observation\n" \
	"observation=boot_materialized\n" \
	"published_benchmark=false\n"

_Static_assert(sizeof(AGENTNEXUS_SEED_CASE_NAME) <= 15,
	       "Nexus case capsule name must fit DIRSIZ");
_Static_assert(sizeof(AGENTNEXUS_SEED_MEAS_NAME) <= 15,
	       "Nexus measurement capsule name must fit DIRSIZ");
_Static_assert(sizeof(AGENTNEXUS_SEED_STATE_NAME) <= 15,
	       "Nexus state capsule name must fit DIRSIZ");
_Static_assert(sizeof(AGENTNEXUS_SEED_CASE_BODY) - 1 <= 1024,
	       "Nexus case capsule exceeds the Guest seed bound");
_Static_assert(sizeof(AGENTNEXUS_SEED_MEAS_BODY) - 1 <= 1024,
	       "Nexus measurement capsule exceeds the Guest seed bound");
_Static_assert(sizeof(AGENTNEXUS_SEED_STATE_BODY) - 1 <= 1024,
	       "Nexus state capsule exceeds the Guest seed bound");

#endif
