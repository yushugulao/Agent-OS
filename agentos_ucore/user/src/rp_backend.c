#include <stdio.h>
#define RP_STATE_BUFFER_SIZE 8192
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
			   "runner=active-user-space;inputs=rp_wfio,rp_stage_state,rp_retry_plan,rp_artifact_manifest;outputs=rp_backend_exec,rp_study\n"
			   "cases=4\n"
			   "executable=2\n"
			   "planned=2\n"
			   "plain_ucore=ready\n"
			   "agentos_ucore=planned\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_backend_exec",
			   "executions=1\n"
			   "workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;case=plain-ucore;source=rp_wfio;status=passed;case=agentos-ucore;source=rp_wfio;status=planned;portability_rehearsal_cases=4;portability_backend_passed=2\n"
			   "runner_cases=4\n"
			   "runner_case=plain-ucore;input=rp_wfio;artifact=rp_artifact_manifest;result=passed;reason=native_programs_ok;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=3\n"
			   "runner_case=retry-recovery;input=rp_retry_plan;artifact=rp_stage_state;result=passed;reason=recovered_align;input_check=pass;artifact_check=pass;att=2;retry=tool_output_missing;ticks=5\n"
			   "runner_case=agentos-context;input=rp_wfio;artifact=agent_context;result=planned;reason=kernel_context;input_check=planned;artifact_check=planned;retry=kernel_required\n"
			   "runner_case=agentos-fsmeta;input=rp_wfio;artifact=agent_file_meta;result=planned;reason=kernel_metadata;input_check=planned;artifact_check=planned;retry=kernel_required\n"
			   "runner_detail=plain-ucore;src=rp_wfio;req=execution_plan;obs=pass;act=record;review=baseline\n"
			   "runner_detail=retry-recovery;src=rp_retry_plan+rp_stage_state;req=retry_stage+stage;obs=pass;act=rerun_align;review=recovered\n"
			   "runner_detail=agentos-context;src=rp_wfio;req=context_path;obs=planned;act=kernel_context;review=target\n"
			   "runner_detail=agentos-fsmeta;src=rp_artifact_manifest;req=metadata_index;obs=planned;act=kernel_fsmeta;review=target\n"
			   "runner_detail_rows=4\n"
			   "runner_detail_schema=src,req,obs,act,review\n"
			   "runner_report=plain-ucore;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;risk=manual_state;status=passed\n"
			   "runner_report=retry-recovery;plain_cost=retry_file_stage_file;agentos_replace=event_context;risk=stale_retry;status=passed\n"
			   "runner_report=agentos-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=planned\n"
			   "runner_report=agentos-fsmeta;plain_cost=scan_records_128;agentos_replace=metadata_index;risk=scan_growth;status=planned\n"
			   "runner_report_rows=4\n"
			   "runner_report_schema=plain_cost,agentos_replace,risk,status\n"
			   "runner_observed=rp_stage_state,rp_retry_plan,rp_artifact_manifest,rp_llmeval\n"
			   "runner_detail_fields=input_check,artifact_check,att,retry,ticks\n"
			   "runner_detail_checks=16\n"
			   "runner_verified_inputs=4\n"
			   "runner_passed=2\n"
			   "runner_planned=2\n"
			   "passed_cases=2\n"
			   "planned_cases=2\n"
			   "indexed_candidate=ready\n"
			   "decision=baseline_ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_study",
			   "study=same-workflow-backend-study\n"
			   "workflow_portability=rp_wfio;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;migration_status=baseline_ready_agentos_planned\n"
			   "study_metric=plain_ucore;file_scans=128;context_trusted=0;rebuild_steps=6;detail_checks=4;result=passed\n"
			   "study_metric=agentos_ucore;context_trusted=1;batch_tools=1;metadata_index=1;detail_checks=kernel;result=planned\n"
			   "study_handoff=rp_backend_exec->rp_agentcmp;status=ready\n"
			   "arms=2\n"
			   "metrics=8\n"
			   "plain_kernel=recorded\n"
			   "agentos_kernel=pending\n"
			   "conclusion=baseline_recorded\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_runner", "backend_evidence_report=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;status=ready")) return 1;
	if (!rp_append_file("rp_report_text", "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=backend;msg=21;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.create_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.record_execution")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.export_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.write_study")) return 1;
	if (!rp_append_status("backend=ready")) return 1;
	if (!rp_append_status("backend_exec=ready")) return 1;
	if (!rp_append_status("study=ready")) return 1;
	printf("rp_backend: cases=4 executable=2 exports=1 status=ready\n");
	return 0;
}
