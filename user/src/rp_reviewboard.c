#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_release", "decision=release");
	ok = ok && rp_file_contains("rp_dossier", "status=ready");
	ok = ok && rp_file_contains("rp_reviewops", "governance=passed");
	ok = ok && rp_file_contains("rp_review_dashboard", "decision=review_pack_ready");
	ok = ok && rp_file_contains("rp_package", "latest_delivery_status=ready");
	ok = ok && rp_file_contains("rp_opsboard", "ready_handoffs=3");
	if (!ok) return 1;

	if (!rp_write_file("rp_reviewboard",
			   "service=formal-review-board\n"
			   "project=lab-gene-x\n"
			   "run_id=RUN-042\n"
			   "review_board_checks=24\n"
			   "review_boards=1\n"
			   "review_requests=1\n"
			   "review_votes=4\n"
			   "review_signoffs=4\n"
			   "review_blockers=0\n"
			   "review_decisions=1\n"
			   "review_assignments=4\n"
			   "review_filters=2\n"
			   "review_workloads=4\n"
			   "review_escalations=0\n"
			   "decision=approved\n"
			   "board=review-board:final-release;chair=wang;members=4;status=active\n"
			   "request=review-request:RUN-042:release-dossier;target=release-dossier:RUN-042:final-review;roles=4;status=approved\n"
			   "vote=review-vote:RUN-042:methods;reviewer=auditor;role=methods_reviewer;decision=approve;status=recorded\n"
			   "vote=review-vote:RUN-042:data;reviewer=data-steward;role=data_reviewer;decision=approve;status=recorded\n"
			   "vote=review-vote:RUN-042:systems;reviewer=systems-reviewer;role=systems_reviewer;decision=approve;status=recorded\n"
			   "vote=review-vote:RUN-042:chair;reviewer=wang;role=release_chair;decision=approve;status=recorded\n"
			   "signoff=review-signoff:RUN-042:methods;signer=auditor;role=methods_reviewer;decision=signed;status=recorded\n"
			   "signoff=review-signoff:RUN-042:data;signer=data-steward;role=data_reviewer;decision=signed;status=recorded\n"
			   "signoff=review-signoff:RUN-042:systems;signer=systems-reviewer;role=systems_reviewer;decision=signed;status=recorded\n"
			   "signoff=review-signoff:RUN-042:chair;signer=wang;role=release_chair;decision=signed;status=recorded\n"
			   "decision_record=review-board-decision:RUN-042:release;approvals=4;rejections=0;blockers_open=0;missing_roles=0;missing_signoffs=0;status=approved\n"
			   "assignment=review-assignment:RUN-042:methods;reviewer=auditor;role=methods_reviewer;priority=medium;status=done\n"
			   "assignment=review-assignment:RUN-042:data;reviewer=data-steward;role=data_reviewer;priority=medium;status=done\n"
			   "assignment=review-assignment:RUN-042:systems;reviewer=systems-reviewer;role=systems_reviewer;priority=medium;status=done\n"
			   "assignment=review-assignment:RUN-042:chair;reviewer=wang;role=release_chair;priority=high;status=done\n"
			   "filter=review-filter:auditor-open;owner=auditor;results=0;status=ready\n"
			   "filter=review-filter:wang-overdue;owner=wang;results=0;status=ready\n"
			   "workload=review-workload:auditor;open=0;overdue=0;high=0;status=ready\n"
			   "workload=review-workload:data-steward;open=0;overdue=0;high=0;status=ready\n"
			   "workload=review-workload:systems-reviewer;open=0;overdue=0;high=0;status=ready\n"
			   "workload=review-workload:wang;open=0;overdue=0;high=0;status=ready\n"
			   "review_package=formal-review-board-package:RUN-042;files=rp_dossier,rp_review_dashboard,rp_package,rp_opsboard;status=ready\n"
			   "agentos_adaptation=capability_review_roles,context_signoff_trace,event_review_queue,metadata_dossier_binding;status=planned\n"
			   "status=ready\n")) {
		return 1;
	}

	if (!rp_append_file("rp_reviewops", "formal_review_board=checks:24;requests:1;votes:4;signoffs:4;assignments:4;decision:approved;status=ready")) return 1;
	if (!rp_append_file("rp_review_dashboard", "subsection=formal_review_board;source=rp_reviewboard;votes=4;signoffs=4;decision=approved;status=ready")) return 1;
	if (!rp_append_file("rp_opsboard", "handoff=review-board->operations;artifact=rp_reviewboard;status=ready")) return 1;
	if (!rp_append_file("rp_agentcmp", "review_board_checks=24;boards=1;requests=1;votes=4;signoffs=4;assignments=4;workloads=4;filters=2;decision=approved;agentos_replacements=4;status=ready")) return 1;
	if (!rp_append_file("rp_ack", "ack=reviewboard;msg=formal-review;status=ready")) return 1;
	if (!rp_append_file("rp_tool", "tool=review_board.open_request")) return 1;
	if (!rp_append_file("rp_tool", "tool=review_board.cast_vote")) return 1;
	if (!rp_append_file("rp_tool", "tool=review_board.record_signoff")) return 1;
	if (!rp_append_file("rp_tool", "tool=review_board.decide")) return 1;
	if (!rp_append_file("rp_tool", "tool=review_operations.sync_assignments")) return 1;
	if (!rp_append_file("rp_tool", "tool=review_operations.export_package")) return 1;
	if (!rp_append_status("reviewboard=ready")) return 1;

	printf("rp_reviewboard: checks=24 requests=1 votes=4 signoffs=4 assignments=4 decision=approved status=ready\n");
	return 0;
}
