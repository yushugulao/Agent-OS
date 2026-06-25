#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	if (!rp_file_contains("rp_review", "status=accepted")) return 1;
	if (!rp_file_contains("rp_review2", "remaining_blockers=0")) return 1;
	if (!rp_file_contains("rp_datadic", "schema_fields=17")) return 1;
	if (!rp_file_contains("rp_labops", "maintenance=passed")) return 1;
	if (!rp_file_contains("rp_mail", "to=writer")) return 1;
	if (!rp_write_file("rp_report",
			   "sections=8\n"
			   "citations=9\n"
			   "response_items=3\n"
			   "data_dictionary=attached\n"
			   "lab_operations=attached\n"
			   "report=RUN-042-recovery\n"
			   "status=packaged\n")) {
		return 1;
	}
	if (!rp_write_file("rp_revision",
			   "rounds=2\n"
			   "draft_versions=3\n"
			   "response_items=3\n"
			   "resolved_comments=3\n"
			   "revision_task=usable-revision-task:RUN-900:1\n"
			   "review_id=usable-review:RUN-900:1\n"
			   "source_run=usable-run:RUN-900\n"
			   "revised_run=usable-run:RUN-900-rev1\n"
			   "applied_changes=2\n"
			   "change=1;target=methods;request=methods_retry_scope;before=retry_scope_implicit;after=retry_scope_explicit;status=applied\n"
			   "change=2;target=chart_caption;request=chart_caption;before=caption_short;after=caption_links_stage_attempts;status=applied\n"
			   "report_delta=methods_and_caption_updated\n"
			   "revision_evidence=rp_review2,rp_report,rp_report_text,rp_chart_data\n"
			   "final_status=ready\n"
			   "status=ready\n")) {
		return 1;
	}
	if (!rp_append_file("rp_ack", "ack=writer;msg=5;status=packaged")) return 1;
	if (!rp_append_file("rp_tool", "tool=writer.assemble_report;target=rp_report;status=ok")) return 1;
	if (!rp_append_file("rp_tool", "tool=writer.apply_revision;target=rp_revision;status=ok")) return 1;
	if (rp_host_seed_has("kind=revision_task")) {
		if (!rp_append_file("rp_revision", "host_action_revision_task=created;status=ready;source=rp_host_action_seed")) return 1;
	}
	if (rp_host_seed_has("kind=revision_run")) {
		if (!rp_append_file("rp_revision", "host_action_revision_run=completed;status=completed;source=rp_host_action_seed")) return 1;
	}
	if (!rp_append_status("writer=packaged")) return 1;
	if (!rp_append_status("revision=ready")) return 1;
	printf("rp_writer: sections=8 citations=9 revisions=3 status=packaged\n");
	return 0;
}
