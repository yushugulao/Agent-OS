#ifndef AGENTNEXUS_SEED_H
#define AGENTNEXUS_SEED_H

/* Tracked ASCII source capsules materialized by the Nexus Guest at boot. */
#define AGENTNEXUS_SEED_VERSION 4U

#define AGENTNEXUS_SEED_PROJECT "agentos-kernel"
#define AGENTNEXUS_SEED_WORKFLOW "live-query-review"
#define AGENTNEXUS_SEED_RUN_ID "BENCH-20260811"

#define AGENTNEXUS_SEED_CASE_NAME "nexus_case"
#define AGENTNEXUS_SEED_MEAS_NAME "nexus_meas"
#define AGENTNEXUS_SEED_STATE_NAME "nexus_state"

#define AGENTNEXUS_SEED_CASE_BODY \
	"schema=agentos.nexus.case.v2\n" \
	"source_contract=agentos.nexus.workflow.v1\n" \
	"seed_revision=4\n" \
	"source_pipeline=watch>query>delegate>plan>govern>publish>audit\n" \
	"source_roles=coordinator,system,research,analyst\n" \
	"nexus_derived_project=agentos-kernel\n" \
	"nexus_derived_workflow=live-query-review\n" \
	"nexus_derived_run_id=BENCH-20260811\n" \
	"nexus_derived_incident=live_query_e2e_gap\n" \
	"nexus_derived_coordination=watch_nonbusy>query>delegate>plan>govern>publish>audit\n" \
	"nexus_derived_required_roles=coordinator,system,research,analyst\n" \
	"nexus_derived_claim=tracked_scenario_capsule\n"

#define AGENTNEXUS_SEED_MEAS_BODY \
	"schema=agentos.nexus.live_query_evidence.v1\n" \
	"perf_source_revision=2b14fb1f74b9bd093e6de939a16554620835699e\n" \
	"source_table=one_shot_metrics/data/20260811/tables/contest_paired.csv\n" \
	"benchmark=live_query_paired\n" \
	"scope=historical_not_this_boot\n" \
	"samples=16\n" \
	"order_balance=8/8\n" \
	"core_us=34712.5/13293.5\n" \
	"core_paired_ratio_median=3.118\n" \
	"core_indexed_wins=16/16\n" \
	"e2e_us=711283.5/723928\n" \
	"e2e_paired_delta_us=13452\n" \
	"e2e_indexed_wins=3/16\n" \
	"outer_us=675901/706477\n" \
	"outer_definition=e2e_minus_core\n" \
	"outer_paired_delta_us=33477\n" \
	"outer_indexed_wins=0/16\n" \
	"records_examined=97/2\n" \
	"workload_syscalls=298/10\n" \
	"core_source=os/agent_metadata_query.c:agent_metadata_query_execute_snapshot\n" \
	"core_sha256=1a95220a0ce3f900f7caaf7ae6f2d3dd58b0d1d6d5461f5253de67b15baab64b\n" \
	"core_mechanism=indexed_candidate_scan\n" \
	"core_constraint=scope_visibility_and_snapshot_stability\n" \
	"outer_source=user/src/labdemo_ucore.c:seed_native_workload\n" \
	"outer_sha256=9e8ccb1d27750a41535324063cca9a93f0f624e569aebb6c4294f5a5b4ff8964\n" \
	"outer_mechanism=corpus_seed_io\n" \
	"claim=historical_snapshot\n"

#define AGENTNEXUS_SEED_STATE_BODY \
	"schema=agentos.nexus.state.v1\n" \
	"source_revision=current_guest_image\n" \
	"nexus_derived_project=agentos-kernel\n" \
	"nexus_derived_workflow=live-query-review\n" \
	"nexus_derived_run_id=BENCH-20260811\n" \
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
