#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_startup", "provider_health=offline:1,cloud:0,ready_cloud:0");
	ok = ok && rp_file_contains("rp_runner", "workbench_next_task=delivery_manifest");
	ok = ok && rp_file_contains("rp_package", "latest_delivery_status=ready");
	ok = ok && rp_file_contains("rp_review_dashboard", "decision=ready_for_reviewer");
	ok = ok && rp_file_contains("rp_runbooks", "runbook_service_checks=16");
	ok = ok && rp_file_contains("rp_projectrel", "project_delivery_checks=18");
	ok = ok && rp_file_contains("rp_studyproto", "study_protocol_checks=20");
	if (!ok) return 1;

	if (!rp_write_file("rp_opsboard",
			   "service=research-operations\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "operations_board_checks=18\n"
			   "provider_health=offline:1,cloud:0,ready_cloud:0\n"
			   "pending_reviews=1\n"
			   "reproduction_package_actions=1\n"
			   "active_workbench_actions=4\n"
			   "active_plan_items=5\n"
			   "active_action_items=4\n"
			   "ready_handoffs=3\n"
			   "latest_runs=4\n"
			   "latest_delivery=ready\n"
			   "operations_reports=1\n"
			   "advance_next_actions=1\n"
			   "execute_next_plan_items=1\n"
			   "export_formats=2\n"
			   "dashboard_pages=1\n"
			   "operation_summary=research-ops:RUN-042;pending_reviews=1;ready_handoffs=3;latest_runs=4;status=ready\n"
			   "queue=workbench-queue:RUN-042;items=4;next=delivery_manifest;status=ready\n"
			   "plan_queue=workbench-plan-queue:RUN-042;items=5;next=build_delivery_manifest;status=ready\n"
			   "action_item=project-action:RUN-042:review-pack;owner=reviewer;priority=high;status=ready\n"
			   "action_item=project-action:RUN-042:delivery-manifest;owner=writer;priority=high;status=waiting\n"
			   "action_item=project-action:RUN-042:protocol-reproduction;owner=recovery;priority=normal;status=ready\n"
			   "action_item=project-action:RUN-042:release-check;owner=auditor;priority=normal;status=ready\n"
			   "advance_result=operations-advance-next:RUN-042;selected=delivery_manifest;effect=rp_package;status=ready\n"
			   "execute_result=operations-execute-plan:RUN-042;selected=build_delivery_manifest;effect=rp_runner;status=ready\n"
			   "report_export=research-ops-report:RUN-042;formats=markdown,json;source=rp_runner,rp_package,rp_review_dashboard;status=ready\n"
			   "handoff=ops->reviewer;artifact=rp_review_dashboard;status=ready\n"
			   "handoff=ops->recovery;artifact=rp_runbooks;status=ready\n"
			   "handoff=ops->auditor;artifact=rp_projectrel;status=ready\n"
			   "source_files=rp_startup,rp_runner,rp_package,rp_review_dashboard,rp_runbooks,rp_projectrel,rp_studyproto\n"
			   "agentos_adaptation=event_queue,context_ops_trace,capability_action_guard,batch_plan_executor;status=planned\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_web_bundle", "research_operations_service=rp_opsboard;checks=18;active_actions=4;handoffs=3;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=research_operations;source=rp_opsboard;pending_reviews=1;handoffs=3;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "research_operations_service=checks:18;pending_reviews:1;active_actions:4;plan_items:5;handoffs:3;exports:2;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=opsboard;msg=research-operations;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=research_ops.summarize")) return 1;
	if (!rp_append_file("rp_tool", "tool=research_ops.advance_next")) return 1;
	if (!rp_append_file("rp_tool", "tool=research_ops.execute_plan_item")) return 1;
	if (!rp_append_file("rp_tool", "tool=research_ops.export_report")) return 1;
	if (!rp_append_file("rp_tool", "tool=research_ops.collect_handoff")) return 1;
	if (!rp_append_status("opsboard=ready")) return 1;

	printf("rp_opsboard: checks=18 pending=1 actions=4 plan_items=5 handoffs=3 status=ready\n");
	return 0;
}
