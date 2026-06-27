#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_execobs", "observer=ready");
	ok = ok && rp_file_contains("rp_worker", "heartbeats=4");
	ok = ok && rp_file_contains("rp_timeline", "critical_path=align_repair");
	ok = ok && rp_file_contains("rp_retry_plan", "failure_reason=tool_output_missing");
	ok = ok && rp_file_contains("rp_runop", "runbook_steps:7");
	ok = ok && rp_file_contains("rp_runop", "worker_ops:6");
	ok = ok && rp_file_contains("rp_package", "delivery_files=8");
	ok = ok && rp_file_contains("rp_review_dashboard", "backend_review_evidence=rp_backend_exec");
	if (!ok) return 1;

	if (!rp_write_file("rp_runbooks",
			   "service=runbooks\n"
			   "run_id=RUN-042\n"
			   "runbook_service_checks=16\n"
			   "runbook_templates=1\n"
			   "runbook_steps=7\n"
			   "incident_triages=1\n"
			   "runbook_executions=1\n"
			   "runbook_exports=1\n"
			   "worker_operation_records=6\n"
			   "execution_observer=rp_execobs\n"
			   "worker_health=rp_worker\n"
			   "timeline_ref=rp_timeline\n"
			   "template=runbook-template:align-oom-recovery;steps=7;owner=recovery;status=ready\n"
			   "step=1;name=detect_failed_stage;input=rp_stage_log;output=incident_triage;status=ready\n"
			   "step=2;name=classify_incident;input=rp_retry_plan;class=resource_limit;severity=high;status=ready\n"
			   "step=3;name=preserve_artifacts;input=rp_artifact_manifest;output=review_pack;status=ready\n"
			   "step=4;name=rerun_align;input=rp_retry_plan;output=rp_stage_state;status=recovered\n"
			   "step=5;name=verify_worker_health;input=rp_worker;heartbeats=4;status=ready\n"
			   "step=6;name=update_report;input=rp_report_text;output=rp_package;status=ready\n"
			   "step=7;name=close_incident;input=rp_review_dashboard;output=release_ready;status=closed\n"
			   "incident=INC-RUN-042-ALIGN-OOM;triage=incident-triage:RUN-042:manual;failed_stage=align;reason=memory_limit;affected_artifacts=rp_artifact_manifest;status=closed\n"
			   "execution=runbook-execution:RUN-042:manual;template=runbook-template:align-oom-recovery;completed_steps=7;retry_stage=align;result=recovered;status=passed\n"
			   "export=runbook-export:RUN-042:manual;format=markdown;package=rp_package;evidence=rp_review_dashboard;status=ready\n"
			   "worker_handoff=worker-a->recovery;queue_action=resume_after_review;failure_classification=resource_limit;status=ready\n"
			   "agentos_adaptation=event_context,kernel_timeline,metadata_index,batch_recovery_tool;status=planned\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_runop", "runbook_service=templates:1,steps:7,incident_triages:1,executions:1,exports:1,worker_records:6,status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=runbooks;source=rp_runbooks;steps=7;incident=closed;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "runbook_service=checks:16;templates:1;steps:7;incident_triages:1;executions:1;exports:1;worker_records:6;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=runbooks;msg=incident;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=runbooks.triage_incident")) return 1;
	if (!rp_append_file("rp_tool", "tool=runbooks.execute_recovery")) return 1;
	if (!rp_append_file("rp_tool", "tool=runbooks.export_package")) return 1;
	if (!rp_append_file("rp_tool", "tool=runbooks.classify_worker_failure")) return 1;
	if (!rp_append_status("runbooks=ready")) return 1;

	printf("rp_runbooks: templates=1 steps=7 incidents=1 executions=1 exports=1 status=ready\n");
	return 0;
}
