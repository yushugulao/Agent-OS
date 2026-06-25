#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_lit", "evidence_links=5")) return 1;
	if (!rp_file_contains("rp_data", "status=needs_repair")) return 1;
	if (!rp_file_contains("rp_mail", "to=reviewer")) return 1;
	if (!rp_write_file("rp_review",
			   "claims=8\nprotocol_checks=5\nrelease_checks=4\ndecision=accepted_after_repair\nstatus=accepted\n")) {
		return 1;
	}
	if (!rp_write_file("rp_review2",
			   "rounds=2\n"
			   "review_threads=2\n"
			   "comments=3\n"
			   "thread=review-thread:RUN-042:methods;target=rp_report_text;status=resolved;participants=reviewer,writer,recovery\n"
			   "thread=review-thread:RUN-042:repro;target=rp_repro;status=resolved;participants=reviewer,auditor,writer\n"
			   "comment=review-comment:RUN-042:1;thread=review-thread:RUN-042:methods;author=reviewer;body=clarify_alignment_retry_scope;refs=rp_retry_plan,rp_stage_log\n"
			   "comment=review-comment:RUN-042:2;thread=review-thread:RUN-042:repro;author=auditor;body=link_manifest_and_reproduction_files;refs=rp_artifact_manifest,rp_repro\n"
			   "comment=review-comment:RUN-042:3;thread=review-thread:RUN-042:methods;author=writer;body=methods_section_updated;refs=rp_revision,rp_report\n"
			   "action_item=action-item:RUN-042:methods;thread=review-thread:RUN-042:methods;owner=writer;priority=high;status=done\n"
			   "action_item=action-item:RUN-042:repro;thread=review-thread:RUN-042:repro;owner=auditor;priority=medium;status=done\n"
			   "human_review=usable-review:RUN-900:1;run=usable-run:RUN-900;reviewer=Wang;decision=needs_revision\n"
			   "requested_change=methods_retry_scope;owner=writer;status=applied\n"
			   "requested_change=chart_caption;owner=writer;status=applied\n"
			   "revision_task=usable-revision-task:RUN-900:1;requested_changes=2;status=completed\n"
			   "action_items=2\n"
			   "review_summary=all_review_comments_resolved\n"
			   "resolved=3\n"
			   "remaining_blockers=0\n"
			   "decision=accepted\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=reviewer;msg=3;status=accepted")) return 1;
	if (!rp_append_file("rp_tool", "tool=reviewer.check_claims;target=rp_review;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=reviewer.multi_round;target=rp_review2;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=reviewer.write_action_items;target=rp_review2;status=ok")) return 1;
	if (!rp_append_status("reviewer=accepted")) return 1;
	if (!rp_append_status("review2=ready")) return 1;
	printf("rp_reviewer: claims=8 protocol_checks=5 release_checks=4 rounds=2 status=accepted\n");
	return 0;
}
