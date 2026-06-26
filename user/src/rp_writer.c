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
	if (!rp_append_file("rp_tool", "tool=writer.assemble_report")) return 1;
	if (!rp_append_file("rp_tool", "tool=writer.apply_revision")) return 1;
	if (rp_host_seed_has("kind=revision_task")) {
		if (!rp_append_file("rp_revision", "host_action_revision_task=created;status=ready;source=rp_host_action_seed")) return 1;
		char targets[80];
		char review_id[80];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_task", "targets=", targets, sizeof(targets))) {
			rp_copy_text(targets, sizeof(targets), "methods,chart_caption");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=revision_task", "review_id=", review_id, sizeof(review_id))) {
			rp_copy_text(review_id, sizeof(review_id), "usable-review:RUN-900:1");
		}
		if (!rp_append_host_action_line("rp_revision", "host_action_revision_targets=", targets)) return 1;
		if (!rp_append_host_action_line("rp_revision", "host_action_revision_review=", review_id)) return 1;
	}
	if (rp_host_seed_has("kind=revision_run")) {
		if (!rp_append_file("rp_revision", "host_action_revision_run=completed;status=completed;source=rp_host_action_seed")) return 1;
		char task_id[80];
		char run_id[48];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_run", "task_id=", task_id, sizeof(task_id))) {
			rp_copy_text(task_id, sizeof(task_id), "usable-revision-task:RUN-900:1");
		}
		if (!rp_host_seed_copy_value_for_kind("kind=revision_run", "run_id=", run_id, sizeof(run_id))) {
			rp_copy_text(run_id, sizeof(run_id), "RUN-900");
		}
		if (!rp_append_host_action_line("rp_revision", "host_action_revision_task_id=", task_id)) return 1;
		if (!rp_append_host_action_line("rp_revision", "host_action_revision_source_run=", run_id)) return 1;
	}
	if (rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_manuscript_audit") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
		if (!rp_append_file("rp_revision", "host_action_workbench_writing=ready;source=rp_host_action_seed")) return 1;
		char value[80];
		if (rp_host_seed_has("kind=workbench_manuscript")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_manuscript", "manuscript_format=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "markdown");
			}
			if (!rp_append_host_action_line("rp_revision", "host_action_workbench_manuscript_format=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_manuscript_audit")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_manuscript_audit", "audit_scope=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "citations");
			}
			if (!rp_append_host_action_line("rp_revision", "host_action_workbench_audit_scope=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_manuscript_revision_plan")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_manuscript_revision_plan", "revision_area=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "methods");
			}
			if (!rp_append_host_action_line("rp_revision", "host_action_workbench_revision_area=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_manuscript_revision_task", "revision_task=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "1");
			}
			if (!rp_append_host_action_line("rp_revision", "host_action_workbench_revision_task=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_manuscript_revision_task", "revision_status=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "done");
			}
			if (!rp_append_host_action_line("rp_revision", "host_action_workbench_revision_status=", value)) return 1;
		}
	}
	if (!rp_append_status("writer=packaged")) return 1;
	if (!rp_append_status("revision=ready")) return 1;
	printf("rp_writer: sections=8 citations=9 revisions=3 status=packaged\n");
	return 0;
}
