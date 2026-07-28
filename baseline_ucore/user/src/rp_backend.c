#include <stdio.h>
#define RP_STATE_BUFFER_SIZE 32768
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_package", "status=ready");
	ok = ok && rp_file_contains("rp_runconf", "candidate=agentos-ucore");
	ok = ok && rp_file_contains("rp_invocation", "status=recovered");
	ok = ok && rp_file_contains("rp_completion", "status=ready");
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_wfio", "execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare");
	ok = ok && rp_file_contains("rp_wfio", "backend_scenario=backend-scenario:RUN-042:agentcompare");
	ok = ok && rp_file_contains("rp_wfio", "compare_profile=compare-profile:RUN-042:migration");
	ok = ok && rp_file_contains("rp_mail", "to=backend");
	ok = ok && rp_file_contains("rp_artifact_manifest", "manifest_records=4");
	ok = ok && rp_file_contains("rp_retry_plan", "retry_stage=align");
	ok = ok && rp_file_contains("rp_stage_state", "stage=align");
	if (!ok) return 1;
	if (!rp_write_file("rp_backend",
			   "scenario=backend-scenario:RUN-042:agentcompare\n"
			   "workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;binding=workflow-migration-binding:RUN-042:plain-ucore\n"
			   "runner=active-user-space;inputs=rp_wfio,rp_stage_state,rp_retry_plan,rp_artifact_manifest,rp_query,rp_run_events,rp_audit;outputs=rp_backend_exec,rp_study\n"
			   "reference_cases=7\n"
			   "catalog_entries=7\n"
			   "runtime_cases=0\n"
			   "runtime_evidence=not_claimed\n"
			   "plain_ucore=ready\n"
			   "userland_equivalents=ready\n"
			   "agentos_ucore=kernel_comparison_target\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_backend_exec",
			   "evidence_file_role=demo_reference\n"
			   "evidence_file_generation=demo_expected\n"
			   "catalog_entries=7\n"
			   "runtime_cases=0\n"
			   "runtime_pass_rows=0\n"
			   "performance_samples=0\n"
			   "workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;reference_case=plain-ucore;source=rp_wfio;expected_status=available;reference_case=agentos-ucore;source=rp_wfio;expected_status=kernel_target;portability_rehearsal_cases=4;reference_cases=7\n"
			   "reference_case=plain-ucore;expected_input=rp_wfio;expected_artifact=rp_artifact_manifest;expected_outcome=native_programs_ok;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "reference_case=retry-recovery;expected_input=rp_retry_plan;expected_artifact=rp_stage_state;expected_outcome=recovered_align;expected_attempts=2;expected_retry=tool_output_missing;status=reference_ready\n"
			   "reference_case=user-context;expected_input=rp_query;expected_artifact=rp_provpath;expected_outcome=user_space_context_log;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "reference_case=user-fsmeta;expected_input=rp_artifact_manifest;expected_artifact=rp_query;expected_outcome=file_manifest_scan;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "reference_case=user-recovery;expected_input=rp_retrylog;expected_artifact=rp_fix;expected_outcome=user_space_repair_record;expected_attempts=2;expected_retry=tool_output_missing;status=reference_ready\n"
			   "reference_case=user-event;expected_input=rp_worker+rp_timeline;expected_artifact=rp_agent_run;expected_outcome=file_backed_event_log;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "reference_case=user-audit;expected_input=rp_audit+rp_provpath;expected_artifact=rp_package;expected_outcome=append_only_audit_files;expected_attempts=1;expected_retry=none;status=reference_ready\n"
			   "runner_report=plain-ucore;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;risk=manual_state;status=reference_ready\n"
			   "runner_report=retry-recovery;plain_cost=retry_file_stage_file;agentos_replace=event_context;risk=stale_retry;status=reference_ready\n"
			   "runner_report=user-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=reference_ready\n"
			   "runner_report=user-fsmeta;plain_cost=scan_records_128;agentos_replace=metadata_index;risk=scan_growth;status=reference_ready\n"
			   "runner_report=user-recovery;plain_cost=manual_retry_contract;agentos_replace=capability_checked_action;risk=wrong_object_update;status=reference_ready\n"
			   "runner_report=user-event;plain_cost=file_polling;agentos_replace=kernel_event_queue;risk=lost_handoff;status=reference_ready\n"
			   "runner_report=user-audit;plain_cost=append_only_logs;agentos_replace=kernel_ledger_provenance;risk=tampered_context;status=reference_ready\n"
			   "reference_case_rows=7\n"
			   "reference_report_rows=7\n"
			   "indexed_candidate=ready\n"
			   "decision=reference_catalog_ready\n"
			   "evidence_file_status=reference_ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_study",
			   "study=same-workflow-backend-study\n"
			   "metric_generation=demo_expected\n"
			   "workflow_portability=rp_wfio;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;migration_status=plain_userland_equivalents_ready\n"
			   "reference_metric=plain_ucore;expected_file_scans=128;expected_context_trusted=0;expected_rebuild_steps=6;status=reference_ready\n"
			   "reference_metric=agentos_ucore;expected_context_trusted=1;expected_batch_tools=1;expected_metadata_index=1;expected_event_queue=1;expected_recovery_tool=1;expected_audit_ledger=1;status=reference_ready\n"
			   "study_handoff=rp_backend_exec->rp_agentcmp;status=ready\n"
			   "arms=2\n"
			   "metrics=12\n"
			   "plain_kernel=recorded\n"
			   "agentos_kernel=target\n"
			   "conclusion=userland_equivalents_ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_runner", "backend_evidence_report=rp_backend_exec;plain_costs=7;agentos_replacements=7;risks=7;status=ready")) return 1;
	if (!rp_append_file("rp_report_text", "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128,manual_retry_contract,file_polling,append_only_logs;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index,capability_checked_action,kernel_event_queue,kernel_ledger_provenance;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=backend;msg=21;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.create_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.record_execution")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.export_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.write_study")) return 1;
	if (!rp_append_status("backend=ready")) return 1;
	if (!rp_append_status("backend_exec=ready")) return 1;
	if (!rp_append_status("study=ready")) return 1;
	printf("rp_backend: evidence_role=demo_reference catalog_generation=demo_expected cases=7 status=reference_ready\n");
	return 0;
}
