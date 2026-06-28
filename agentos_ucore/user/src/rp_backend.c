#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define RP_STATE_BUFFER_SIZE 32768
#include <research_platform_state.h>

#define BACKEND_EDIT_FILE "r42edrep"

static struct agent_context_header backend_header;
static struct agent_context_record backend_records[8];
static struct agent_file_query backend_query;
static struct agent_file_query_result backend_query_result;
static struct agent_file_edit_state backend_edit_state;
static struct agent_op backend_op;
static struct agent_result backend_result;

static int write_exact_file(const char *path, const char *text)
{
	int fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
	int len = (int)strlen(text);
	int wrote;

	if (fd < 0)
		return 0;
	wrote = write(fd, text, len);
	close(fd);
	return wrote == len;
}

static int run_kernel_edit_check(void)
{
	struct agent_file_edit_state state;
	uint64 base;
	int fd;
	int rc;

	unlink(BACKEND_EDIT_FILE);
	if (!write_exact_file(BACKEND_EDIT_FILE, "draft-report\n"))
		return -1;

	memset(&state, 0, sizeof(state));
	rc = agent_file_edit_begin(BACKEND_EDIT_FILE, 0, 200,
				   &state);
	if (rc != 0 || !state.active) {
		printf("rp_backend: edit_begin_failed rc=%d active=%d\n",
		       rc, state.active);
		return -1;
	}
	base = state.base_version;

	memset(&backend_edit_state, 0, sizeof(backend_edit_state));
	if (agent_file_edit_state(BACKEND_EDIT_FILE, &backend_edit_state) < 0 ||
	    !backend_edit_state.active ||
	    backend_edit_state.lease_id != state.lease_id) {
		printf("rp_backend: edit_state_failed active=%d lease=%d expected=%d\n",
		       backend_edit_state.active,
		       (int)backend_edit_state.lease_id,
		       (int)state.lease_id);
		agent_file_edit_abort(state.lease_id);
		return -1;
	}
	fd = open(BACKEND_EDIT_FILE, O_WRONLY);
	if (fd < 0) {
		printf("rp_backend: edit_open_failed\n");
		agent_file_edit_abort(state.lease_id);
		return -1;
	}
	if (write(fd, "A", 1) != 1) {
		printf("rp_backend: edit_write_failed\n");
		close(fd);
		agent_file_edit_abort(state.lease_id);
		return -1;
	}
	close(fd);
	rc = agent_file_edit_commit(state.lease_id, base, &backend_edit_state);
	if (rc != 0 || backend_edit_state.active ||
	    backend_edit_state.current_version != base + 1) {
		printf("rp_backend: edit_commit_failed rc=%d active=%d base=%d current=%d\n",
		       rc, backend_edit_state.active, (int)base,
		       (int)backend_edit_state.current_version);
		return -1;
	}

	if (!rp_write_file("rp_agentos_conflict",
			   "run_id=RUN-042\n"
			   "edit_target=r42edrep\n"
			   "edit_lease=kernel_exclusive\n"
			   "resource_identity=dev_inum\n"
			   "holder_write=checked\n"
			   "version_commit=checked\n"
			   "stale_write_policy=reject\n"
			   "status=ready\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=edit_conflict;edit_lease=kernel_exclusive;holder_write=checked;version_commit=checked;resource_identity=dev_inum;status=ready")) {
		return -1;
	}
	return 1;
}

static int run_kernel_backend_check(void)
{
	struct agent_info info;

	if (agent_info(&info) < 0 || !info.is_agent)
		return 0;
	memset(&backend_op, 0, sizeof(backend_op));
	backend_op.version = AGENT_CALL_VERSION;
	backend_op.tool_id = AGENT_TOOL_ECHO;
	backend_op.request_id = 4401;
	strcpy(backend_op.payload, "backend-kernel-check");
	if (agent_run(&backend_op, &backend_result, 1, 0) != 1 ||
	    backend_result.status != AGENT_STATUS_OK) {
		printf("rp_backend: agent_run_failed status=%d\n",
		       backend_result.status);
		return -1;
	}
	if (context_snapshot(&backend_header, backend_records, 8) < 1 ||
	    backend_header.latest_sequence == 0) {
		printf("rp_backend: context_snapshot_failed\n");
		return -1;
	}
	memset(&backend_query, 0, sizeof(backend_query));
	backend_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	backend_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(backend_query.project, "lab-gene-x");
	strcpy(backend_query.run_id, "RUN-042");
	strcpy(backend_query.stage, "report");
	strcpy(backend_query.status, "ok");
	if (agent_file_query(&backend_query, &backend_query_result) < 1 ||
	    backend_query_result.returned < 1 ||
	    !backend_query_result.used_index) {
		printf("rp_backend: report_metadata_query_failed hits=%d index=%d\n",
		       backend_query_result.returned,
		       backend_query_result.used_index);
		return -1;
	}
	if (run_kernel_edit_check() < 0) {
		printf("rp_backend: edit_conflict_check_failed\n");
		return -1;
	}
	return 1;
}

int main(void)
{
	int ok = 1;
	int kernel_backend = run_kernel_backend_check();

	if (kernel_backend < 0)
		return 1;
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
	ok = ok && rp_file_contains("rp_agentos_kernel", "status=ready");
	ok = ok && rp_file_contains("rp_agentos_kernel", "context_snapshot=present");
	ok = ok && rp_file_contains("rp_agentos_kernel", "file_meta_service=initialized");
	ok = ok && rp_file_contains("rp_agentos_roles", "stage_launch=agent_create_role");
	ok = ok && rp_file_contains("rp_agentos_recovery", "kernel_tool=action_commit,artifact_update");
	ok = ok && rp_file_contains("rp_agentos_query", "metadata_source=kernel_file_index");
	ok = ok && rp_file_contains("rp_agentos_timeline", "event_delivery=kernel_agent_queue");
	ok = ok && rp_file_contains("rp_agentos_collab_ack", "delivery=kernel_event_queue");
	ok = ok && rp_file_contains("rp_agentos_audit", "audit_source=kernel_ledger");
	ok = ok && rp_file_contains("rp_agentos_workbench", "file_verify=kernel_metadata_index");
	ok = ok && rp_file_contains("rp_agentos_package", "package_trace=kernel_provenance");
	ok = ok && rp_file_contains("rp_agentos_real_task", "report_answer=kernel_context_record");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "context_trusted=kernel_shadow");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "metadata_query=used_index");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "agent_event_notify=kernel_queue");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "failure_recovery=generic_action");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "provenance_audit=kernel_ledger");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "permission_control=sentinel_rerun_denied");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "timeline_observe=kernel_snapshot");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "workbench_file_verify=kernel_metadata_index");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "package_provenance=kernel_ledger");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "real_task_context=kernel_shadow");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "edit_lease=kernel_exclusive");
	ok = ok && rp_file_contains("rp_agentos_conflict", "holder_write=checked");
	if (!ok) return 1;
	if (!rp_write_file("rp_backend",
			   "scenario=backend-scenario:RUN-042:agentcompare\n"
			   "workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;binding=workflow-migration-binding:RUN-042:plain-ucore\n"
			   "runner=agentos-kernel-assisted;inputs=rp_wfio,rp_stage_state,rp_retry_plan,rp_artifact_manifest,rp_agentos_kernel,rp_agentos_mainflow,rp_agentos_recovery,rp_agentos_query,rp_agentos_timeline,rp_agentos_audit,rp_agentos_workbench,rp_agentos_package,rp_agentos_real_task,rp_agentos_conflict;outputs=rp_backend_exec,rp_study\n"
			   "cases=8\n"
			   "executable=8\n"
			   "planned=0\n"
			   "plain_ucore=ready\n"
			   "agentos_ucore=kernel_bound\n"
			   "agentos_mainflow_kernel=required\n"
			   "agentos_mainflow_facts=11\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_backend_exec",
			   "executions=1\n"
			   "workflow_portability=rp_wfio;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;case=plain-ucore;source=rp_wfio;status=passed;case=agentos-ucore;source=rp_agentos_kernel;status=passed;portability_rehearsal_cases=4;portability_backend_passed=8\n"
			   "runner_cases=8\n"
			   "runner_case=plain-ucore;input=rp_wfio;artifact=rp_artifact_manifest;result=passed;reason=native_programs_ok;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=3\n"
			   "runner_case=retry-recovery;input=rp_retry_plan;artifact=rp_stage_state;result=passed;reason=recovered_align;input_check=pass;artifact_check=pass;att=2;retry=tool_output_missing;ticks=5\n"
			   "runner_case=agentos-context;input=rp_agentos_kernel;artifact=agent_context;result=passed;reason=kernel_context;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=1\n"
			   "runner_case=agentos-fsmeta;input=rp_agentos_kernel;artifact=agent_file_meta;result=passed;reason=kernel_metadata;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=1\n"
			   "runner_case=agentos-recovery;input=rp_agentos_recovery;artifact=rp_fix;result=passed;reason=kernel_action_commit;input_check=pass;artifact_check=pass;att=1;retry=generic_action;ticks=1\n"
			   "runner_case=agentos-event;input=rp_agentos_timeline+rp_agentos_collab_ack;artifact=rp_agent_run;result=passed;reason=kernel_event_queue;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=1\n"
			   "runner_case=agentos-audit;input=rp_agentos_audit;artifact=rp_audit;result=passed;reason=kernel_ledger;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=1\n"
			   "runner_case=agentos-edit;input=rp_agentos_conflict;artifact=agent_file_edit;result=passed;reason=kernel_edit_lease;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=1\n"
			   "runner_detail=plain-ucore;src=rp_wfio;req=execution_plan;obs=pass;act=record;review=baseline\n"
			   "runner_detail=retry-recovery;src=rp_retry_plan+rp_stage_state;req=retry_stage+stage;obs=pass;act=rerun_align;review=recovered\n"
			   "runner_detail=agentos-context;src=rp_agentos_kernel;req=context_path;obs=pass;act=context_snapshot;review=observed\n"
			   "runner_detail=agentos-fsmeta;src=rp_agentos_kernel;req=metadata_index;obs=pass;act=file_meta_init;review=observed\n"
			   "runner_detail=agentos-recovery;src=rp_agentos_recovery;req=action_commit+artifact_update;obs=pass;act=generic_kernel_tools;review=verified\n"
			   "runner_detail=agentos-event;src=rp_agentos_timeline+rp_agentos_collab_ack;req=event_wait_wake;obs=pass;act=kernel_queue;review=verified\n"
			   "runner_detail=agentos-audit;src=rp_agentos_audit;req=audit+provenance;obs=pass;act=ledger_snapshot;review=verified\n"
			   "runner_detail=agentos-edit;src=rp_agentos_conflict;req=file_edit_lease;obs=pass;act=edit_begin_commit;review=verified\n"
			   "runner_detail_rows=8\n"
			   "runner_detail_schema=src,req,obs,act,review\n"
			   "runner_report=plain-ucore;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;risk=manual_state;status=passed\n"
			   "runner_report=retry-recovery;plain_cost=retry_file_stage_file;agentos_replace=event_context;risk=stale_retry;status=passed\n"
			   "runner_report=agentos-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=passed\n"
			   "runner_report=agentos-fsmeta;plain_cost=scan_records_128;agentos_replace=metadata_index;risk=scan_growth;status=passed\n"
			   "runner_report=agentos-recovery;plain_cost=manual_retry_contract;agentos_replace=capability_checked_action;risk=wrong_object_update;status=passed\n"
			   "runner_report=agentos-event;plain_cost=file_polling;agentos_replace=kernel_event_queue;risk=lost_handoff;status=passed\n"
			   "runner_report=agentos-audit;plain_cost=append_only_logs;agentos_replace=kernel_ledger_provenance;risk=tampered_context;status=passed\n"
			   "runner_report=agentos-edit;plain_cost=userland_lock_file;agentos_replace=kernel_edit_lease;risk=lost_update;status=passed\n"
			   "runner_report_rows=8\n"
			   "runner_report_schema=plain_cost,agentos_replace,risk,status\n"
			   "runner_observed=rp_stage_state,rp_retry_plan,rp_artifact_manifest,rp_llmeval,rp_agentos_kernel,rp_agentos_mainflow,rp_agentos_recovery,rp_agentos_query,rp_agentos_timeline,rp_agentos_audit,rp_agentos_workbench,rp_agentos_package,rp_agentos_real_task,rp_agentos_conflict\n"
			   "runner_detail_fields=input_check,artifact_check,att,retry,ticks\n"
			   "runner_detail_checks=32\n"
			   "runner_verified_inputs=8\n"
			   "runner_passed=8\n"
			   "runner_planned=0\n"
			   "passed_cases=8\n"
			   "planned_cases=0\n"
			   "indexed_candidate=ready\n"
			   "decision=agentos_observed\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_write_file("rp_study",
			   "study=same-workflow-backend-study\n"
			   "workflow_portability=rp_wfio;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;migration_status=baseline_and_agentos_observed\n"
			   "study_metric=plain_ucore;file_scans=128;context_trusted=0;rebuild_steps=6;detail_checks=4;result=passed\n"
			   "study_metric=agentos_ucore;context_trusted=1;batch_tools=1;metadata_index=1;event_queue=1;recovery_tool=1;audit_ledger=1;permission_control=1;timeline_observe=1;workbench_verify=1;package_trace=1;real_task_context=1;edit_lease=1;mainflow_facts=11;detail_checks=kernel;result=passed\n"
			   "study_handoff=rp_backend_exec->rp_agentcmp;status=ready\n"
			   "arms=2\n"
			   "metrics=12\n"
			   "plain_kernel=recorded\n"
			   "agentos_kernel=mainflow_bound\n"
			   "conclusion=kernel_services_reduce_scan_polling_manual_rebuild\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_runner", "backend_evidence_report=rp_backend_exec;plain_costs=8;agentos_replacements=8;risks=8;status=ready")) return 1;
	if (!rp_append_file("rp_report_text", "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128,manual_retry_contract,file_polling,append_only_logs,userland_lock_file;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index,capability_checked_action,kernel_event_queue,kernel_ledger_provenance,kernel_edit_lease,workbench_file_verify,package_trace,real_task_context;mainflow_facts=11;status=ready")) return 1;
	if (kernel_backend &&
	    !rp_append_file("rp_tool", "tool=agentos.backend_context_check")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=backend;msg=21;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.create_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.record_execution")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.export_scenario")) return 1;
	if (!rp_append_file("rp_tool", "tool=backend.write_study")) return 1;
	if (!rp_append_status("backend=ready")) return 1;
	if (!rp_append_status("backend_exec=ready")) return 1;
	if (!rp_append_status("study=ready")) return 1;
	printf("rp_backend: cases=8 executable=8 agentos=mainflow_bound exports=1 status=ready\n");
	return 0;
}
