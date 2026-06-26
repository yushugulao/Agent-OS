#include <stdio.h>
#include <research_platform_state.h>

static void append_workbench_summary_value(char *line, int cap, const char *kind, const char *key, const char *prefix, const char *fallback)
{
	char value[96];
	if (!rp_host_seed_copy_value_for_kind(kind, key, value, sizeof(value))) {
		rp_copy_text(value, sizeof(value), fallback);
	}
	rp_append_text(line, cap, prefix);
	rp_append_text(line, cap, value);
	rp_append_text(line, cap, ";");
}

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

	if (!rp_append_file("rp_runner", "section=rp_workbench_ready;question_present=1;imported_inputs=1;literature_evidence=1;generated_artifacts=1;llm_trace=ready;human_review=needs_revision;delivery_manifest=waiting;package_export=ready;next_action=build_delivery_manifest;ready_status=ready")) return 1;
	if (!rp_append_file("rp_runner", "section=rp_workbench_answer;answer_id=usable-workbench-answer:RUN-900:1;question=What is ready and what still needs review?;citation_count=5;citation=rp_input:workspace_import;citation=rp_runner:custom_analysis;citation=rp_knowledge:evidence_synthesis;citation=rp_llm_resp:response_join;citation=rp_package:delivery_manifest;missing_item=delivery_manifest_finalization;answer_status=ready")) return 1;
	if (!rp_append_file("rp_runner", "section=rp_workbench_brief;brief_id=usable-workbench-brief:RUN-900:1;latest_run=usable-run:RUN-903;latest_answer=usable-workbench-answer:RUN-900:1;evidence_ids=5;next_actions=2;file_paths=rp_input,rp_runner,rp_knowledge,rp_package,rp_workbench_manifest;handoff=ready;brief_status=ready")) return 1;
	if (!rp_append_file("rp_runner", "section=rp_workbench_runbook;commands=6;command=check_readiness;command=advance_delivery_manifest;command=answer_from_evidence;command=export_file_manifest;command=package_reviewer_bundle;command=open_review_page;continuation_guide=ready;runbook_status=ready")) return 1;
	if (!rp_append_file("rp_runner", "section=rp_workbench_timeline;events=8;event=created;source=rp_input;event=inspected;source=rp_runner;event=imported;source=rp_input;event=searched;source=rp_knowledge;event=screened;source=rp_knowledge;event=run;source=rp_runner;event=reviewed;source=rp_review2;event=exported;source=rp_package;timeline_status=ready")) return 1;
	if (!rp_append_file("rp_runner", "section=rp_workbench_manifest;files=9;sha_records=9;file=rp_input;kind=input;file=rp_runner;kind=run;file=rp_knowledge;kind=evidence;file=rp_review2;kind=review;file=rp_revision;kind=revision;file=rp_package;kind=delivery;file=rp_llm_resp;kind=llm;file=rp_artifact_manifest;kind=artifact;file=rp_report_text;kind=report;manifest_status=ready")) return 1;

	if (rp_host_seed_has("kind=workbench") ||
	    rp_host_seed_has("kind=workbench_complete") ||
	    rp_host_seed_has("kind=workbench_advance") ||
	    rp_host_seed_has("kind=workbench_auto_advance") ||
	    rp_host_seed_has("kind=workbench_task") ||
	    rp_host_seed_has("kind=workbench_note") ||
	    rp_host_seed_has("kind=workbench_notes") ||
	    rp_host_seed_has("kind=workbench_handoff_package") ||
	    rp_host_seed_has("kind=workbench_readiness") ||
	    rp_host_seed_has("kind=workbench_answer") ||
	    rp_host_seed_has("kind=workbench_answer_audit") ||
	    rp_host_seed_has("kind=workbench_evidence_search") ||
	    rp_host_seed_has("kind=workbench_brief") ||
	    rp_host_seed_has("kind=workbench_evidence_dossier") ||
	    rp_host_seed_has("kind=workbench_evidence_graph") ||
	    rp_host_seed_has("kind=workbench_citations") ||
	    rp_host_seed_has("kind=workbench_manuscript") ||
	    rp_host_seed_has("kind=workbench_manuscript_audit") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_plan") ||
	    rp_host_seed_has("kind=workbench_manuscript_revision_task") ||
	    rp_host_seed_has("kind=workbench_task_board") ||
	    rp_host_seed_has("kind=workbench_task_board_row") ||
	    rp_host_seed_has("kind=workbench_runbook") ||
	    rp_host_seed_has("kind=workbench_timeline") ||
	    rp_host_seed_has("kind=workbench_file_manifest") ||
	    rp_host_seed_has("kind=workbench_file_verify") ||
	    rp_host_seed_has("kind=workbench_export")) {
		if (!rp_append_file("rp_runner", "host_action_workbench=completed;status=ready;source=rp_host_action_seed")) return 1;
		if (!rp_append_file("rp_runner", "host_action_workbench_next=delivery_manifest_ready")) return 1;
		char core[1024];
		int has_core = 0;
		rp_copy_text(core, sizeof(core), "host_action_workbench_core=");
		if (rp_host_seed_has("kind=workbench_answer")) {
			has_core = 1;
			rp_append_text(core, sizeof(core), "host_action_workbench_answer=generated;");
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_answer", "question=", "host_action_workbench_question=", "What is ready for review?");
		}
		if (rp_host_seed_has("kind=workbench_evidence_search")) {
			has_core = 1;
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_evidence_search", "query=", "host_action_workbench_evidence_query=", "recovery evidence");
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_evidence_search", "query=", "host_action_workbench_query=", "recovery evidence");
		}
		if (rp_host_seed_has("kind=workbench_task")) {
			has_core = 1;
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_task", "workbench=", "host_action_workbench_id=", "usable-workbench:RUN-900");
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_task", "task=", "host_action_workbench_task=", "human_review");
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_task", "status=", "host_action_workbench_task_status=", "waiting");
		}
		if (rp_host_seed_has("kind=workbench_note")) {
			has_core = 1;
			rp_append_text(core, sizeof(core), "host_action_workbench_note=recorded;");
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_note", "note_kind=", "host_action_workbench_note_kind=", "decision");
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_note", "title=", "host_action_workbench_note_title=", "Scope decision");
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_note", "body=", "host_action_workbench_note_body=", "Use recovered evidence first.");
		}
		if (rp_host_seed_has("kind=workbench_file_verify")) {
			has_core = 1;
			rp_append_text(core, sizeof(core), "host_action_workbench_file_verify=passed;");
			append_workbench_summary_value(core, sizeof(core), "kind=workbench_file_verify", "manifest=", "host_action_workbench_manifest=", "delivery-manifest.json");
		}
		if (has_core) {
			if (!rp_append_file("rp_runner", core)) return 1;
		}

		char ops[960];
		int has_ops = 0;
		rp_copy_text(ops, sizeof(ops), "host_action_workbench_ops=");
		if (rp_host_seed_has("kind=workbench")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_created=1;");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench", "workbench=", "host_action_workbench_id=", "usable-workbench:RUN-900");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench", "workbench_title=", "host_action_workbench_title=", "RUN-900 workbench");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench", "literature_query=", "host_action_workbench_literature_query=", "agent workflow provenance");
		}
		if (rp_host_seed_has("kind=workbench_complete")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_completion=ready;");
		}
		if (rp_host_seed_has("kind=workbench_advance")) {
			has_ops = 1;
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench_advance", "task=", "host_action_workbench_task=", "delivery_manifest");
		}
		if (rp_host_seed_has("kind=workbench_auto_advance")) {
			has_ops = 1;
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench_auto_advance", "step_limit=", "host_action_workbench_step_limit=", "8");
		}
		if (rp_host_seed_has("kind=workbench_notes")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_notes=exported;");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench_notes", "notes_filter=", "host_action_workbench_notes_filter=", "decision");
		}
		if (rp_host_seed_has("kind=workbench_handoff_package")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_handoff=prepared;");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench_handoff_package", "handoff_scope=", "host_action_workbench_handoff_scope=", "full");
		}
		if (rp_host_seed_has("kind=workbench_readiness")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_readiness=checked;");
		}
		if (rp_host_seed_has("kind=workbench_answer_audit")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_answer_audit=passed;");
		}
		if (rp_host_seed_has("kind=workbench_manuscript_revision_plan")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_revision_plan=ready;");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench_manuscript_revision_plan", "revision_area=", "host_action_workbench_revision_area=", "methods");
		}
		if (rp_host_seed_has("kind=workbench_manuscript_revision_task")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_revision_task=updated;");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench_manuscript_revision_task", "revision_task=", "host_action_workbench_revision_task=", "1");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench_manuscript_revision_task", "revision_status=", "host_action_workbench_revision_status=", "done");
		}
		if (rp_host_seed_has("kind=workbench_task_board")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_task_board=exported;");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench_task_board", "board_filter=", "host_action_workbench_board_filter=", "open");
		}
		if (rp_host_seed_has("kind=workbench_task_board_row")) {
			has_ops = 1;
			rp_append_text(ops, sizeof(ops), "host_action_workbench_task_board_row=updated;");
			append_workbench_summary_value(ops, sizeof(ops), "kind=workbench_task_board_row", "row_status=", "host_action_workbench_row_status=", "done");
		}
		if (has_ops) {
			if (!rp_append_file("rp_runner", ops)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_task_board_row")) {
			char row[192];
			rp_copy_text(row, sizeof(row), "host_action_workbench_row=");
			append_workbench_summary_value(row, sizeof(row), "kind=workbench_task_board_row", "row_id=", "host_action_workbench_row_id=", "usable-workbench:RUN-900:board:task:human_review");
			if (!rp_append_file("rp_runner", row)) return 1;
		}

		char docs[1024];
		int has_docs = 0;
		rp_copy_text(docs, sizeof(docs), "host_action_workbench_docs=");
		if (rp_host_seed_has("kind=workbench_brief")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_brief=exported;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_brief", "brief_format=", "host_action_workbench_brief_format=", "html");
		}
		if (rp_host_seed_has("kind=workbench_evidence_dossier")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_evidence_dossier=exported;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_evidence_dossier", "dossier_format=", "host_action_workbench_dossier_format=", "markdown");
		}
		if (rp_host_seed_has("kind=workbench_evidence_graph")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_evidence_graph=exported;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_evidence_graph", "graph_format=", "host_action_workbench_graph_format=", "dot");
		}
		if (rp_host_seed_has("kind=workbench_citations")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_citations=exported;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_citations", "citation_format=", "host_action_workbench_citation_format=", "bibtex");
		}
		if (rp_host_seed_has("kind=workbench_manuscript")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_manuscript=exported;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_manuscript", "manuscript_format=", "host_action_workbench_manuscript_format=", "markdown");
		}
		if (rp_host_seed_has("kind=workbench_manuscript_audit")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_manuscript_audit=passed;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_manuscript_audit", "audit_scope=", "host_action_workbench_audit_scope=", "citations");
		}
		if (rp_host_seed_has("kind=workbench_runbook")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_runbook=exported;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_runbook", "runbook_format=", "host_action_workbench_runbook_format=", "markdown");
		}
		if (rp_host_seed_has("kind=workbench_timeline")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_timeline=exported;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_timeline", "timeline_format=", "host_action_workbench_timeline_format=", "html");
		}
		if (rp_host_seed_has("kind=workbench_file_manifest")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_file_manifest=exported;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_file_manifest", "manifest=", "host_action_workbench_manifest=", "delivery-manifest.json");
		}
		if (rp_host_seed_has("kind=workbench_export")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_export=ready;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_export", "bundle=", "host_action_workbench_bundle=", "workbench-bundle.zip");
		}
		if (has_docs) {
			if (!rp_append_file("rp_runner", docs)) return 1;
		}
	}

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
