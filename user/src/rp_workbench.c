#include <stdio.h>
#include <research_platform_state.h>

int main(void)
{
	int ok = 1;
	ok = ok && rp_file_contains("rp_input", "request_form=form_fields=8");
	ok = ok && rp_file_contains("rp_input", "upload_files=uploads=2");
	ok = ok && rp_file_contains("rp_input", "workspace_import=workspace:RUN-900:folder");
	ok = ok && rp_file_contains("rp_input", "workspace_template=usable-template:workspace-900");
	ok = ok && rp_file_contains("rp_input", "workspace_run=usable-run:RUN-903");
	ok = ok && rp_file_contains("rp_input", "library_source_id=usable-source:library2026:1");
	ok = ok && rp_file_contains("rp_knowledge", "literature_search_id=usable-literature-search:RUN-900:1");
	ok = ok && rp_file_contains("rp_knowledge", "evidence_protocol=usable-evidence-protocol:RUN-900:1");
	ok = ok && rp_file_contains("rp_knowledge", "evidence_synthesis=usable-evidence-synthesis:RUN-900:1");
	ok = ok && rp_file_contains("rp_runner", "custom_runs=3");
	ok = ok && rp_file_contains("rp_runner", "revision_run=usable-run:RUN-900-rev1");
	ok = ok && rp_file_contains("rp_runner", "citation_plan_entries=3");
	if (!ok) return 1;

	if (!rp_append_file("rp_runner", "workbench=usable-workbench:RUN-900:plain-ucore")) return 1;
	if (!rp_append_file("rp_runner", "workbench_tasks=9")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task_done=8")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task_waiting=1")) return 1;
	if (!rp_append_file("rp_runner", "workbench_next_task=delivery_manifest")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=inspect_workspace;status=done")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=import_workspace;status=done")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=discover_literature;status=done")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=review_evidence;status=done")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=build_protocol;status=done")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=run_template;status=done;run=usable-run:RUN-903")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=human_review;status=done;review=usable-review:RUN-900:1")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=delivery_manifest;status=waiting;delivery=usable-delivery:RUN-900:1")) return 1;
	if (!rp_append_file("rp_runner", "workspace_inspection=usable-workspace-inspection:RUN-900:1;files=4;recommendation=ready")) return 1;
	if (!rp_append_file("rp_runner", "workspace_import=usable-workspace-import:RUN-900:1;template=usable-template:workspace-900;run=usable-run:RUN-903")) return 1;
	if (!rp_append_file("rp_runner", "workspace_file=metadata.tsv;kind=metadata;rows=3")) return 1;
	if (!rp_append_file("rp_runner", "workbench_export=usable-workbench-export:RUN-900:1;bundle=workbench-bundle.zip;included_files=9")) return 1;
	if (!rp_append_file("rp_runner", "workbench_export_child_run=usable-run:RUN-900")) return 1;
	if (!rp_append_file("rp_runner", "workbench_export_child_run=usable-run:RUN-903")) return 1;
	if (!rp_append_file("rp_ack", "ack=workbench;msg=research_board;status=ready")) return 1;
	if (!rp_append_status("workbench=ready")) return 1;
	if (!rp_append_status("workbench_tasks=ready")) return 1;
	if (!rp_append_status("workspace_inspection=ready")) return 1;
	if (!rp_append_status("workspace_import_service=ready")) return 1;
	if (!rp_append_status("workbench_export=ready")) return 1;
	printf("rp_workbench: tasks=9 workspace_files=4 runs=4 exports=1 status=ready\n");
	return 0;
}
