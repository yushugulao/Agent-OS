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
	if (!rp_append_file("rp_runner", "workbench_readiness=rp_workbench_ready;status=ready")) return 1;
	if (!rp_append_file("rp_runner", "workbench_answer=rp_workbench_answer;citations=5;status=ready")) return 1;
	if (!rp_append_file("rp_runner", "workbench_brief=rp_workbench_brief;handoff=ready")) return 1;
	if (!rp_append_file("rp_runner", "workbench_runbook=rp_workbench_runbook;commands=6")) return 1;
	if (!rp_append_file("rp_runner", "workbench_timeline=rp_workbench_timeline;events=8")) return 1;
	if (!rp_append_file("rp_runner", "workbench_file_manifest=rp_workbench_manifest;files=9;sha_records=9")) return 1;

	if (!rp_append_file("rp_runner",
			   "section=rp_workbench_ready\n"
			   "question_present=1\n"
			   "imported_inputs=1\n"
			   "literature_evidence=1\n"
			   "generated_artifacts=1\n"
			   "llm_trace=ready\n"
			   "human_review=needs_revision\n"
			   "delivery_manifest=waiting\n"
			   "package_export=ready\n"
			   "next_action=build_delivery_manifest\n"
			   "ready_status=ready")) return 1;
	if (!rp_append_file("rp_runner",
			   "section=rp_workbench_answer\n"
			   "answer_id=usable-workbench-answer:RUN-900:1\n"
			   "question=What is ready and what still needs review?\n"
			   "answer=workspace import, literature evidence, run artifacts, LLM trace, and human review are ready; delivery manifest remains the next action\n"
			   "citation_count=5\n"
			   "citation=rp_input:workspace_import\n"
			   "citation=rp_runner:custom_analysis\n"
			   "citation=rp_knowledge:evidence_synthesis\n"
			   "citation=rp_llm_resp:response_join\n"
			   "citation=rp_package:delivery_manifest\n"
			   "missing_item=delivery_manifest_finalization\n"
			   "answer_status=ready")) return 1;
	if (!rp_append_file("rp_runner",
			   "section=rp_workbench_brief\n"
			   "brief_id=usable-workbench-brief:RUN-900:1\n"
			   "latest_run=usable-run:RUN-903\n"
			   "latest_answer=usable-workbench-answer:RUN-900:1\n"
			   "evidence_ids=5\n"
			   "next_actions=2\n"
			   "next_action=build_delivery_manifest\n"
			   "next_action=export_reviewer_bundle\n"
			   "file_paths=rp_input,rp_runner,rp_knowledge,rp_package,rp_workbench_manifest\n"
			   "handoff=ready\n"
			   "brief_status=ready")) return 1;
	if (!rp_append_file("rp_runner",
			   "section=rp_workbench_runbook\n"
			   "commands=6\n"
			   "command=check_readiness\n"
			   "command=advance_delivery_manifest\n"
			   "command=answer_from_evidence\n"
			   "command=export_file_manifest\n"
			   "command=package_reviewer_bundle\n"
			   "command=open_review_page\n"
			   "continuation_guide=ready\n"
			   "runbook_status=ready")) return 1;
	if (!rp_append_file("rp_runner",
			   "section=rp_workbench_timeline\n"
			   "events=8\n"
			   "event=created;source=rp_input\n"
			   "event=inspected;source=rp_runner\n"
			   "event=imported;source=rp_input\n"
			   "event=searched;source=rp_knowledge\n"
			   "event=screened;source=rp_knowledge\n"
			   "event=run;source=rp_runner\n"
			   "event=reviewed;source=rp_review2\n"
			   "event=exported;source=rp_package\n"
			   "timeline_status=ready")) return 1;
	if (!rp_append_file("rp_runner",
			   "section=rp_workbench_manifest\n"
			   "files=9\n"
			   "sha_records=9\n"
			   "file=rp_input;kind=input;sha=plain-hash-001\n"
			   "file=rp_runner;kind=run;sha=plain-hash-002\n"
			   "file=rp_knowledge;kind=evidence;sha=plain-hash-003\n"
			   "file=rp_review2;kind=review;sha=plain-hash-004\n"
			   "file=rp_revision;kind=revision;sha=plain-hash-005\n"
			   "file=rp_package;kind=delivery;sha=plain-hash-006\n"
			   "file=rp_llm_resp;kind=llm;sha=plain-hash-007\n"
			   "file=rp_artifact_manifest;kind=artifact;sha=plain-hash-008\n"
			   "file=rp_report_text;kind=report;sha=plain-hash-009\n"
			   "manifest_status=ready")) return 1;

	if (!rp_append_file("rp_ack", "ack=workbench;msg=research_board;status=ready")) return 1;
	if (!rp_append_status("workbench=ready")) return 1;
	if (!rp_append_status("workbench_tasks=ready")) return 1;
	if (!rp_append_status("workspace_inspection=ready")) return 1;
	if (!rp_append_status("workspace_import_service=ready")) return 1;
	if (!rp_append_status("workbench_export=ready")) return 1;
	if (!rp_append_status("workbench_readiness=ready")) return 1;
	if (!rp_append_status("workbench_answer=ready")) return 1;
	if (!rp_append_status("workbench_brief=ready")) return 1;
	if (!rp_append_status("workbench_runbook=ready")) return 1;
	if (!rp_append_status("workbench_timeline=ready")) return 1;
	if (!rp_append_status("workbench_manifest=ready")) return 1;
	printf("rp_workbench: tasks=9 workspace_files=4 runs=4 exports=7 status=ready\n");
	return 0;
}
