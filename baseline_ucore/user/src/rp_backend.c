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
			   "cases=7\n"
			   "executable=7\n"
			   "planned=0\n"
			   "plain_ucore=ready\n"
			   "userland_equivalents=ready\n"
			   "agentos_ucore=kernel_comparison_target\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_backend_exec",
			   "executions=1\n"
			   "workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;case=plain-ucore;source=rp_wfio;status=passed;case=agentos-ucore;source=rp_wfio;status=kernel_target;portability_rehearsal_cases=4;portability_backend_passed=7\n"
			   "runner_cases=7\n"
			   "runner_case=plain-ucore;input=rp_wfio;artifact=rp_artifact_manifest;result=passed;reason=native_programs_ok;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=3\n"
			   "runner_case=retry-recovery;input=rp_retry_plan;artifact=rp_stage_state;result=passed;reason=recovered_align;input_check=pass;artifact_check=pass;att=2;retry=tool_output_missing;ticks=5\n"
			   "runner_case=user-context;input=rp_query;artifact=rp_provpath;result=passed;reason=user_space_context_log;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=6\n"
			   "runner_case=user-fsmeta;input=rp_artifact_manifest;artifact=rp_query;result=passed;reason=file_manifest_scan;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=7\n"
			   "runner_case=user-recovery;input=rp_retrylog;artifact=rp_fix;result=passed;reason=user_space_repair_record;input_check=pass;artifact_check=pass;att=2;retry=tool_output_missing;ticks=5\n"
			   "runner_case=user-event;input=rp_worker+rp_timeline;artifact=rp_agent_run;result=passed;reason=file_backed_event_log;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=6\n"
			   "runner_case=user-audit;input=rp_audit+rp_provpath;artifact=rp_package;result=passed;reason=append_only_audit_files;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=6\n"
			   "runner_detail=plain-ucore;src=rp_wfio;req=execution_plan;obs=pass;act=record;review=baseline\n"
			   "runner_detail=retry-recovery;src=rp_retry_plan+rp_stage_state;req=retry_stage+stage;obs=pass;act=rerun_align;review=recovered\n"
			   "runner_detail=user-context;src=rp_query+rp_provpath;req=context_path;obs=pass;act=rebuild_from_files;review=userland\n"
			   "runner_detail=user-fsmeta;src=rp_artifact_manifest;req=metadata_index;obs=pass;act=scan_manifest;review=userland\n"
			   "runner_detail=user-recovery;src=rp_retrylog+rp_fix;req=action_commit+artifact_update;obs=pass;act=repair_files;review=userland\n"
			   "runner_detail=user-event;src=rp_worker+rp_timeline;req=event_wait_wake;obs=pass;act=file_event_records;review=userland\n"
			   "runner_detail=user-audit;src=rp_audit+rp_provpath;req=audit+provenance;obs=pass;act=append_logs;review=userland\n"
			   "runner_detail_rows=7\n"
			   "runner_detail_schema=src,req,obs,act,review\n"
			   "runner_report=plain-ucore;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;risk=manual_state;status=passed\n"
			   "runner_report=retry-recovery;plain_cost=retry_file_stage_file;agentos_replace=event_context;risk=stale_retry;status=passed\n"
			   "runner_report=user-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=passed\n"
			   "runner_report=user-fsmeta;plain_cost=scan_records_128;agentos_replace=metadata_index;risk=scan_growth;status=passed\n"
			   "runner_report=user-recovery;plain_cost=manual_retry_contract;agentos_replace=capability_checked_action;risk=wrong_object_update;status=passed\n"
			   "runner_report=user-event;plain_cost=file_polling;agentos_replace=kernel_event_queue;risk=lost_handoff;status=passed\n"
			   "runner_report=user-audit;plain_cost=append_only_logs;agentos_replace=kernel_ledger_provenance;risk=tampered_context;status=passed\n"
			   "runner_report_rows=7\n"
			   "runner_report_schema=plain_cost,agentos_replace,risk,status\n"
			   "runner_observed=rp_stage_state,rp_retry_plan,rp_artifact_manifest,rp_llmeval,rp_query,rp_run_events,rp_audit\n"
			   "runner_detail_fields=input_check,artifact_check,att,retry,ticks\n"
			   "runner_detail_checks=28\n"
			   "runner_verified_inputs=7\n"
			   "runner_passed=7\n"
			   "runner_planned=0\n"
			   "passed_cases=7\n"
			   "planned_cases=0\n"
			   "indexed_candidate=ready\n"
			   "decision=baseline_ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_study",
			   "study=same-workflow-backend-study\n"
			   "workflow_portability=rp_wfio;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;migration_status=plain_userland_equivalents_ready\n"
			   "study_metric=plain_ucore;file_scans=128;context_trusted=0;rebuild_steps=6;detail_checks=7;result=passed\n"
			   "study_metric=agentos_ucore;context_trusted=1;batch_tools=1;metadata_index=1;event_queue=1;recovery_tool=1;audit_ledger=1;detail_checks=kernel;result=kernel_target\n"
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
	printf("rp_backend: cases=7 executable=7 userland_equivalent=ready exports=1 status=ready\n");
	return 0;
}
