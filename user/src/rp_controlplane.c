#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_reviewboard", "decision=approved");
	ok = ok && rp_file_contains("rp_opsboard", "status=ready");
	ok = ok && rp_file_contains("rp_package", "latest_delivery_status=ready");
	ok = ok && rp_file_contains("rp_web_bundle", "status=ready");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "permission_control=sentinel_action_denied");
	ok = ok && rp_file_contains("rp_agentos_mainflow", "agent_event_notify=kernel_queue");
	ok = ok && rp_file_contains("rp_agentos_roles", "stage_launch=agent_create_role");
	ok = ok && rp_file_contains("rp_agentos_timeline", "timeline_snapshot=ready");
	ok = ok && rp_file_contains("rp_agentos_kernel", "agent_run=echo");
	if (!ok) return 1;

	if (!rp_write_file("rp_control",
			   "service=platform-control-plane\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "control_plane_checks=30\n"
			   "approvals=4\n"
			   "approval_transitions=4\n"
			   "subscriptions=3\n"
			   "notifications=4\n"
			   "run_queue_items=4\n"
			   "leases=2\n"
			   "plugin_manifests=3\n"
			   "plugin_runs=3\n"
			   "workspaces=1\n"
			   "users=3\n"
			   "access_grants=3\n"
			   "saved_views=2\n"
			   "api_tokens=1\n"
			   "permissions=5\n"
			   "control_actions=8\n"
			   "approval=approval:release-dossier:1;target=release-dossier:RUN-042;state=draft;actor=writer;status=recorded\n"
			   "approval=approval:release-dossier:2;target=release-dossier:RUN-042;state=submitted;actor=writer;status=recorded\n"
			   "approval=approval:release-dossier:3;target=release-dossier:RUN-042;state=approved;actor=wang;status=recorded\n"
			   "approval=approval:release-dossier:4;target=release-dossier:RUN-042;state=published;actor=wang;status=recorded\n"
			   "subscription=sub:review:wang:APPROVAL_STATE;target=wang;event=APPROVAL_STATE;status=active\n"
			   "subscription=sub:review:auditor:QUEUE_ITEM_FINISHED;target=auditor;event=QUEUE_ITEM_FINISHED;status=active\n"
			   "subscription=sub:ops:writer:*;target=writer;event=*;status=active\n"
			   "notification=notif:1;target=wang;event=APPROVAL_STATE;delivered=1;status=ready\n"
			   "notification=notif:2;target=auditor;event=QUEUE_ITEM_FINISHED;delivered=1;status=ready\n"
			   "notification=notif:3;target=writer;event=RUN_LEASED;delivered=1;status=ready\n"
			   "notification=notif:4;target=writer;event=PLUGIN_RUN;delivered=1;status=ready\n"
			   "queue=queue:RUN-042:1;run=RUN-042;priority=90;state=done;worker=orchestrator;status=ready\n"
			   "queue=queue:RUN-042:2;run=RUN-042-review;priority=80;state=leased;worker=reviewer;status=ready\n"
			   "queue=queue:RUN-042:3;run=RUN-042-package;priority=70;state=queued;worker=none;status=ready\n"
			   "queue=queue:RUN-042:4;run=RUN-042-audit;priority=60;state=done;worker=auditor;status=ready\n"
			   "plugin=plugin.artifacts;name=Artifact Analytics;tools=artifact_count_by_status;enabled=1;status=ready\n"
			   "plugin=plugin.failures;name=Failure Summaries;tools=stage_failure_summary;enabled=1;status=ready\n"
			   "plugin=plugin.tuning;name=Parameter Tuning;tools=recommend_memory_limit;enabled=1;status=ready\n"
			   "plugin_run=plugin-run:1;plugin=plugin.artifacts;tool=artifact_count_by_status;result=ok;status=ready\n"
			   "plugin_run=plugin-run:2;plugin=plugin.failures;tool=stage_failure_summary;result=ok;status=ready\n"
			   "plugin_run=plugin-run:3;plugin=plugin.tuning;tool=recommend_memory_limit;current=1024;recommended=1536;status=ready\n"
			   "workspace=ws:lab-gene-x;owner=wang;projects=1;status=ready\n"
			   "user=user:wang;roles=maintainer;status=ready\n"
			   "user=user:auditor;roles=auditor;status=ready\n"
			   "user=user:guest;roles=viewer;status=ready\n"
			   "grant=grant:wang:lab-gene-x:maintainer;subject=wang;object=lab-gene-x;role=maintainer;status=ready\n"
			   "grant=grant:auditor:lab-gene-x:auditor;subject=auditor;object=lab-gene-x;role=auditor;status=ready\n"
			   "grant=grant:guest:lab-gene-x:viewer;subject=guest;object=lab-gene-x;role=viewer;status=ready\n"
			   "saved_view=view:failed-artifacts;kind=artifacts;query=status=failed;owner=wang;status=ready\n"
			   "saved_view=view:planned-jobs;kind=jobs;query=status=planned;owner=wang;status=ready\n"
			   "api_token=token:local-dashboard;owner=wang;scopes=read,dashboard;secret_material=not_written;status=ready\n"
			   "permission=can:wang:approve;result=allow;status=ready\n"
			   "permission=can:wang:admin;result=allow;status=ready\n"
			   "permission=can:auditor:audit;result=allow;status=ready\n"
			   "permission=can:guest:write;result=deny;status=ready\n"
			   "permission=can:guest:approve;result=deny;status=ready\n"
			   "control_report=platform-control-report:RUN-042;approvals=4;notifications=4;queue_items=4;plugin_runs=3;status=ready\n"
			   "agentos_adaptation=kernel_capability_check,kernel_event_delivery,kernel_plugin_tool_table,kernel_run_queue;evidence=rp_agentos_mainflow,rp_agentos_roles,rp_agentos_timeline,rp_agentos_kernel;result=observed;status=ready\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_web_bundle", "platform_control_plane=rp_control;approvals=4;notifications=4;queue_items=4;plugins=3;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=platform_control_plane;source=rp_control;approvals=4;notifications=4;plugins=3;status=ready")) return 1;
	if (!rp_append_file("rp_opsboard", "handoff=control-plane->operations;artifact=rp_control;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "control_plane_checks=30;approvals=4;notifications=4;queue_items=4;plugins=3;workspaces=1;permissions=5;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "control_plane_kernel_binding=capability_check,event_delivery,tool_table,agent_run_queue;source=rp_control;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=controlplane;msg=platform-control;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=approval.submit")) return 1;
	if (!rp_append_file("rp_tool", "tool=approval.approve")) return 1;
	if (!rp_append_file("rp_tool", "tool=notification.publish")) return 1;
	if (!rp_append_file("rp_tool", "tool=run_queue.lease_next")) return 1;
	if (!rp_append_file("rp_tool", "tool=run_queue.complete")) return 1;
	if (!rp_append_file("rp_tool", "tool=plugin.call")) return 1;
	if (!rp_append_file("rp_tool", "tool=workspace.grant")) return 1;
	if (!rp_append_file("rp_tool", "tool=workspace.can")) return 1;
	if (!rp_append_status("controlplane=ready")) return 1;

	printf("rp_controlplane: checks=30 approvals=4 notifications=4 queue=4 plugins=3 permissions=5 status=ready\n");
	return 0;
}
