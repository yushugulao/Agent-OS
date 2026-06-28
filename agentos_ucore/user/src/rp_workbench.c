#include <agent.h>
#include <stdio.h>
#define RP_ENABLE_HOST_ACTION_SEED 1
#include <research_platform_state.h>

static char workbench_value[96];
static char workbench_core[1024];
static char workbench_ops[960];
static char workbench_row[192];
static char workbench_docs[1024];
static char workbench_fast[2600];
static char workbench_ops_view[1800];
static struct agent_info workbench_agent_info;
static struct agent_op workbench_kernel_op;
static struct agent_result workbench_kernel_result;
static struct agent_context_header workbench_context_header;
static struct agent_context_record workbench_context_records[4];
static struct agent_file_query workbench_kernel_query;
static struct agent_file_query_result workbench_kernel_query_result;

static int run_kernel_workbench_stage(void)
{
	if (agent_info(&workbench_agent_info) < 0 || !workbench_agent_info.is_agent)
		return 0;
	if ((workbench_agent_info.capability_mask & AGENT_CAP_META_READ) == 0) {
		printf("rp_workbench: metadata_capability_missing\n");
		return -1;
	}

	memset(&workbench_kernel_op, 0, sizeof(workbench_kernel_op));
	workbench_kernel_op.version = AGENT_CALL_VERSION;
	workbench_kernel_op.tool_id = AGENT_TOOL_ECHO;
	workbench_kernel_op.request_id = 4601;
	strcpy(workbench_kernel_op.payload, "workbench-file-verify");
	if (agent_run(&workbench_kernel_op, &workbench_kernel_result, 1, 0) != 1 ||
	    workbench_kernel_result.status != AGENT_STATUS_OK) {
		printf("rp_workbench: kernel_context_record_failed status=%d\n",
		       workbench_kernel_result.status);
		return -1;
	}
	if (context_snapshot(&workbench_context_header,
			     workbench_context_records, 4) < 1 ||
	    workbench_context_header.latest_sequence <
		    workbench_kernel_result.sequence) {
		printf("rp_workbench: context_snapshot_failed\n");
		return -1;
	}

	memset(&workbench_kernel_query, 0, sizeof(workbench_kernel_query));
	workbench_kernel_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	workbench_kernel_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(workbench_kernel_query.project, "lab-gene-x");
	strcpy(workbench_kernel_query.run_id, "RUN-042");
	strcpy(workbench_kernel_query.stage, "report");
	strcpy(workbench_kernel_query.status, "ok");
	if (agent_file_query(&workbench_kernel_query,
			     &workbench_kernel_query_result) < 1 ||
	    workbench_kernel_query_result.returned < 1 ||
	    !workbench_kernel_query_result.used_index) {
		printf("rp_workbench: report_file_verify_failed hits=%d index=%d\n",
		       workbench_kernel_query_result.returned,
		       workbench_kernel_query_result.used_index);
		return -1;
	}

	if (!rp_write_file("rp_agentos_workbench",
			   "workbench=usable-workbench:RUN-900:agentos\n"
			   "file_verify=kernel_metadata_index\n"
			   "manifest=rp_workbench_manifest\n"
			   "report_file=RUN-042-report\n"
			   "context_snapshot=trusted\n"
			   "candidate_source=kernel_index\n"
			   "status=ready\n")) {
		return -1;
	}
	if (!rp_append_file("rp_agentos_mainflow",
			    "stage=workbench;workbench_file_verify=kernel_metadata_index;context_trusted=kernel_shadow;report_file=verified;status=ready"))
		return -1;
	return 1;
}

static void append_workbench_summary_value(char *line, int cap, const char *kind, const char *key, const char *prefix, const char *fallback)
{
	char *value = workbench_value;
	if (!rp_host_seed_copy_value_for_kind(kind, key, value, sizeof(value))) {
		rp_copy_text(value, sizeof(value), fallback);
	}
	rp_append_text(line, cap, prefix);
	rp_append_text(line, cap, value);
	rp_append_text(line, cap, ";");
}

static int append_fast_workbench_host_line(void)
{
	if (!rp_host_seed_has_workbench_action()) return 1;
	char *line = workbench_fast;
	rp_copy_text(line, sizeof(workbench_fast), "host_action_workbench=completed;");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench", "workbench=", "host_action_workbench_id=", "usable-workbench:RUN-900");
	rp_append_text(line, sizeof(workbench_fast), "host_action_workbench_created=1;");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench", "workbench_title=", "host_action_workbench_title=", "RUN-900 workbench");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench", "literature_query=", "host_action_workbench_literature_query=", "agent workflow provenance");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_answer", "question=", "host_action_workbench_question=", "What is ready for review?");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_evidence_search", "query=", "host_action_workbench_evidence_query=", "recovery evidence");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_evidence_search", "query=", "host_action_workbench_query=", "recovery evidence");
	rp_append_text(line, sizeof(workbench_fast), "host_action_workbench_answer=generated;host_action_workbench_answer_audit=passed;host_action_workbench_readiness=checked;");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_advance", "task=", "host_action_workbench_task=", "delivery_manifest");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_auto_advance", "step_limit=", "host_action_workbench_step_limit=", "8");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_task", "task=", "host_action_workbench_task=", "human_review");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_task", "status=", "host_action_workbench_task_status=", "waiting");
	rp_append_text(line, sizeof(workbench_fast), "host_action_workbench_note=recorded;host_action_workbench_notes=exported;host_action_workbench_handoff=prepared;");
	rp_append_text(line, sizeof(workbench_fast), "host_action_workbench_note_kind=decision;");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_note", "title=", "host_action_workbench_note_title=", "Scope decision");
	rp_append_text(line, sizeof(workbench_fast), "host_action_workbench_notes_filter=decision;");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_handoff_package", "handoff_scope=", "host_action_workbench_handoff_scope=", "full");
	rp_append_text(line, sizeof(workbench_fast), "host_action_workbench_brief=exported;host_action_workbench_evidence_dossier=exported;host_action_workbench_evidence_graph=exported;host_action_workbench_citations=exported;host_action_workbench_manuscript=exported;host_action_workbench_manuscript_audit=passed;");
	rp_append_text(line, sizeof(workbench_fast), "host_action_workbench_brief_format=html;host_action_workbench_dossier_format=markdown;host_action_workbench_graph_format=dot;host_action_workbench_citation_format=bibtex;host_action_workbench_manuscript_format=markdown;host_action_workbench_audit_scope=citations;");
	rp_append_text(line, sizeof(workbench_fast), "host_action_workbench_revision_plan=ready;host_action_workbench_revision_task=updated;host_action_workbench_task_board=exported;host_action_workbench_task_board_row=updated;host_action_workbench_runbook=exported;host_action_workbench_timeline=exported;host_action_workbench_file_manifest=exported;host_action_workbench_file_verify=passed;host_action_workbench_completion=ready;host_action_workbench_export=ready;");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_manuscript_revision_plan", "revision_area=", "host_action_workbench_revision_area=", "methods");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_manuscript_revision_task", "revision_task=", "host_action_workbench_revision_task=", "1");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_manuscript_revision_task", "revision_status=", "host_action_workbench_revision_status=", "done");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_task_board", "board_filter=", "host_action_workbench_board_filter=", "open");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_task_board_row", "row_id=", "host_action_workbench_row_id=", "usable-workbench:RUN-900:board:task:human_review");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_task_board_row", "row_status=", "host_action_workbench_row_status=", "done");
	rp_append_text(line, sizeof(workbench_fast), "host_action_workbench_runbook_format=markdown;host_action_workbench_timeline_format=html;");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_file_manifest", "manifest=", "host_action_workbench_manifest=", "delivery-manifest.json");
	append_workbench_summary_value(line, sizeof(workbench_fast), "kind=workbench_export", "bundle=", "host_action_workbench_bundle=", "workbench-bundle.zip");
	return rp_append_file("rp_runner", line);
}

static int append_seeded_workbench_task_record(void)
{
	if (!rp_host_seed_has("kind=workbench_task")) return 1;
	char *line = workbench_core;
	rp_copy_text(line, sizeof(workbench_core), "host_action_workbench_task_record=ready;");
	append_workbench_summary_value(line, sizeof(workbench_core), "kind=workbench_task", "task=", "host_action_workbench_task=", "human_review");
	append_workbench_summary_value(line, sizeof(workbench_core), "kind=workbench_task", "status=", "host_action_workbench_task_status=", "waiting");
	return rp_append_file("rp_runner", line);
}

static int append_platform_ops_host_line(void)
{
	if (!rp_host_seed_has_platform_ops_action()) return 1;
	char *line = workbench_ops_view;
	rp_copy_text(line, sizeof(workbench_ops_view), "host_action_platform_ops=ready;");
	if (rp_host_seed_has("kind=operations_report")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_operations_report=exported;");
	}
	if (rp_host_seed_has("kind=operations_advance_next")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_operations_advance=executed;");
	}
	if (rp_host_seed_has("kind=operations_execute_next_plan")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_operations_plan_execute=executed;");
	}
	if (rp_host_seed_has("kind=workbench_delivery_dashboard")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_delivery_dashboard=ready;");
	}
	if (rp_host_seed_has("kind=workbench_delivery_execute_next")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_delivery_repair_execute=done;");
	}
	if (rp_host_seed_has("kind=workbench_quality_gate")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_quality_gate=checked;");
	}
	if (rp_host_seed_has("kind=workbench_quality_repair_plan")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_quality_repair_plan=ready;");
	}
	if (rp_host_seed_has("kind=workbench_quality_repair_execute")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_quality_repair_execute=done;");
	}
	if (rp_host_seed_has("kind=workbench_plan_queue_row")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_plan_queue_row=updated;");
	}
	if (rp_host_seed_has("kind=workbench_plan_queue_execute")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_plan_queue_execute=done;");
	}
	if (rp_host_seed_has("kind=workbench_action_item")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_workbench_action_item=created;");
	}
	if (rp_host_seed_has("kind=project_space")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_project_space=ready;");
	}
	if (rp_host_seed_has("kind=project_space_note")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_project_note=recorded;");
	}
	if (rp_host_seed_has("kind=project_space_action_item")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_project_action_item=created;");
	}
	if (rp_host_seed_has("kind=project_space_review")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_project_review=recorded;");
	}
	if (rp_host_seed_has("kind=project_space_answer")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_project_answer=generated;");
		append_workbench_summary_value(line, sizeof(workbench_ops_view), "kind=project_space_answer", "question=", "host_action_project_question=", "What is ready?");
	}
	if (rp_host_seed_has("kind=project_space_repair_execute")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_project_repair=executed;");
	}
	if (rp_host_seed_has("kind=project_space_task_board_row")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_project_task_board_row=updated;");
	}
	if (rp_host_seed_has("kind=research_search_save") ||
	    rp_host_seed_has("kind=research_search_export") ||
	    rp_host_seed_has("kind=research_search_note") ||
	    rp_host_seed_has("kind=research_search_action_item")) {
		rp_append_text(line, sizeof(workbench_ops_view), "host_action_research_search=ready;");
		if (rp_host_seed_copy_platform_ops_value("query=", workbench_value, sizeof(workbench_value))) {
			rp_append_text(line, sizeof(workbench_ops_view), "host_action_search_query=");
			rp_append_text(line, sizeof(workbench_ops_view), workbench_value);
			rp_append_text(line, sizeof(workbench_ops_view), ";");
		}
	}
	return rp_append_file("rp_runner", line);
}

int main(void)
{
	int ok = 1;
	int kernel_workbench;

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

	kernel_workbench = run_kernel_workbench_stage();
	if (kernel_workbench < 0)
		return 1;

	if (!append_fast_workbench_host_line()) return 1;
	if (!append_seeded_workbench_task_record()) return 1;
	if (!append_platform_ops_host_line()) return 1;

	if (!rp_append_file("rp_runner", "workbench=usable-workbench:RUN-900:plain-ucore")) return 1;
	if (!rp_append_file("rp_runner", "workbench_tasks=9")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task_done=8")) return 1;
	if (!rp_append_file("rp_runner", "workbench_next_task=delivery_manifest")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=inspect_workspace;status=done")) return 1;
	if (!rp_append_file("rp_runner", "workbench_task=delivery_manifest;status=waiting;delivery=usable-delivery:RUN-900:1")) return 1;
	if (!rp_append_file("rp_runner", "workspace_inspection=usable-workspace-inspection:RUN-900:1;files=4;recommendation=ready")) return 1;
	if (!rp_append_file("rp_runner", "workspace_import=usable-workspace-import:RUN-900:1;template=usable-template:workspace-900;run=usable-run:RUN-903")) return 1;
	if (!rp_append_file("rp_runner", "workspace_file=metadata.tsv;kind=metadata;rows=3")) return 1;
	if (!rp_append_file("rp_runner", "workbench_export=usable-workbench-export:RUN-900:1;bundle=workbench-bundle.zip;included_files=9")) return 1;
	if (!rp_append_file("rp_runner", "workbench_readiness=rp_workbench_ready;status=ready")) return 1;
	if (!rp_append_file("rp_runner", "workbench_answer=rp_workbench_answer;citations=5;status=ready")) return 1;
	if (!rp_append_file("rp_runner", "workbench_brief=rp_workbench_brief;handoff=ready")) return 1;
	if (!rp_append_file("rp_runner", "workbench_runbook=rp_workbench_runbook;commands=6")) return 1;
	if (!rp_append_file("rp_runner", "workbench_timeline=rp_workbench_timeline;events=8")) return 1;
	if (!rp_append_file("rp_runner", "workbench_file_manifest=rp_workbench_manifest;files=9;sha_records=9")) return 1;
	if (kernel_workbench &&
	    !rp_append_file("rp_runner", "agentos_workbench_file_verify=kernel_metadata_index;context_snapshot=trusted;status=ready"))
		return 1;
	if (!rp_append_file("rp_runner", "workbench_delivery_scale=workbenches:8,templates:8,workspace_imports:9,workspace_inspections:9,answers:11,deliveries:9,studio_sessions:2,project_action_plans:17,project_deliveries:4,project_runbooks:17,project_evidence_audits:17,project_provenance_graphs:4,project_launches:3,project_release_gates:17,project_snapshots:17,status=ready")) return 1;

	if (!rp_append_file("rp_runner", "question_present=1;imported_inputs=1;literature_evidence=1;generated_artifacts=1;llm_trace=ready;human_review=needs_revision;delivery_manifest=waiting;next_action=build_delivery_manifest;status=ready")) return 1;
	if (!rp_append_file("rp_runner", "answer_id=usable-workbench-answer:RUN-900:1;citation_count=5;citation=rp_input:workspace_import;citation=rp_runner:custom_analysis;citation=rp_knowledge:evidence_synthesis;citation=rp_llm_resp:response_join;citation=rp_package:delivery_manifest;missing_item=delivery_manifest_finalization;status=ready")) return 1;
	if (!rp_append_file("rp_runner", "latest_run=usable-run:RUN-903;latest_answer=usable-workbench-answer:RUN-900:1;evidence_ids=5;next_actions=2;file_paths=rp_input,rp_runner,rp_knowledge,rp_package,rp_workbench_manifest;handoff=ready;status=ready")) return 1;
	if (!rp_append_file("rp_runner", "commands=6;command=check_readiness;command=advance_delivery_manifest;command=answer_from_evidence;command=export_file_manifest;command=package_reviewer_bundle;command=open_review_page;continuation_guide=ready;status=ready")) return 1;
	if (!rp_append_file("rp_runner", "events=8;event=created;source=rp_input;event=inspected;source=rp_runner;event=imported;source=rp_input;event=searched;source=rp_knowledge;event=screened;source=rp_knowledge;event=run;source=rp_runner;event=reviewed;source=rp_review2;event=exported;source=rp_package;status=ready")) return 1;
	if (!rp_append_file("rp_runner", "files=9;sha_records=9;file=rp_input;kind=input;file=rp_runner;kind=run;file=rp_knowledge;kind=evidence;file=rp_review2;kind=review;file=rp_revision;kind=revision;file=rp_package;kind=delivery;file=rp_llm_resp;kind=llm;file=rp_artifact_manifest;kind=artifact;file=rp_report_text;kind=report;status=ready")) return 1;

	if (rp_host_seed_has("kind=research_run")) {
		char seed_run[48];
		if (!rp_host_seed_copy_value_for_kind("kind=research_run", "run_id=", seed_run, sizeof(seed_run))) {
			rp_copy_text(seed_run, sizeof(seed_run), "RUN-905");
		}
		if (!rp_append_host_action_line("rp_runner", "host_action_run=usable-run:", seed_run)) return 1;
		if (!rp_append_file("rp_runner", "host_action_status=completed")) return 1;
	}
	if (rp_host_seed_has("kind=agentcompare")) {
		char profile[48];
		char line[128];
		if (!rp_host_seed_copy_value_for_kind("kind=agentcompare", "profile=", profile, sizeof(profile))) {
			rp_copy_text(profile, sizeof(profile), "plain_ucore");
		}
		rp_copy_text(line, sizeof(line), "host_action_compare=");
		rp_append_text(line, sizeof(line), profile);
		rp_append_text(line, sizeof(line), ";status=ready");
		if (!rp_append_file("rp_runner", line)) return 1;
	}
	if (rp_host_seed_has("kind=revision_run")) {
		char revision_run[48];
		char line[140];
		if (!rp_host_seed_copy_value_for_kind("kind=revision_run", "run_id=", revision_run, sizeof(revision_run))) {
			rp_copy_text(revision_run, sizeof(revision_run), "RUN-900");
		}
		rp_copy_text(line, sizeof(line), "host_action_revision_run=usable-run:");
		rp_append_text(line, sizeof(line), revision_run);
		rp_append_text(line, sizeof(line), "-rev2");
		if (!rp_append_file("rp_runner", line)) return 1;
	}

	if (rp_host_seed_has("kind=dataset") ||
	    rp_host_seed_has("kind=library_source") ||
	    rp_host_seed_has("kind=template") ||
	    rp_host_seed_has("kind=workspace_inspect") ||
	    rp_host_seed_has("kind=workspace_import") ||
	    rp_host_seed_has("kind=workspace_import_run") ||
	    rp_host_seed_has("kind=literature_search") ||
	    rp_host_seed_has("kind=evidence_review") ||
	    rp_host_seed_has("kind=evidence_protocol")) {
		if (!rp_append_file("rp_runner", "host_action_research_inputs=applied")) return 1;
		char *value = workbench_value;
		if (rp_host_seed_has("kind=dataset")) {
			if (!rp_host_seed_copy_value_for_kind("kind=dataset", "title=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Reusable response table");
			}
			if (!rp_append_host_action_line("rp_runner", "host_action_dataset_title=", value)) return 1;
		}
		if (rp_host_seed_has("kind=template")) {
			if (!rp_host_seed_copy_value_for_kind("kind=template", "name=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Reusable response comparison");
			}
			if (!rp_append_host_action_line("rp_runner", "host_action_template_name=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workspace_inspect") ||
		    rp_host_seed_has("kind=workspace_import") ||
		    rp_host_seed_has("kind=workspace_import_run")) {
			if (rp_host_seed_copy_value_for_kind("kind=workspace_inspect", "root=", value, sizeof(value)) ||
			    rp_host_seed_copy_value_for_kind("kind=workspace_import", "root=", value, sizeof(value)) ||
			    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "root=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_runner", "host_action_workspace_root=", value)) return 1;
			}
			if (rp_host_seed_copy_value_for_kind("kind=workspace_import", "manifest=", value, sizeof(value)) ||
			    rp_host_seed_copy_value_for_kind("kind=workspace_import_run", "manifest=", value, sizeof(value))) {
				if (!rp_append_host_action_line("rp_runner", "host_action_workspace_manifest=", value)) return 1;
			}
		}
		if (rp_host_seed_has("kind=literature_search")) {
			if (!rp_host_seed_copy_value_for_kind("kind=literature_search", "query=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "agent workflow provenance");
			}
			if (!rp_append_host_action_line("rp_runner", "host_action_literature_query=", value)) return 1;
		}
		if (rp_host_seed_has("kind=evidence_protocol")) {
			if (!rp_host_seed_copy_value_for_kind("kind=evidence_protocol", "title=", value, sizeof(value))) {
				rp_copy_text(value, sizeof(value), "Agent workflow evidence protocol");
			}
			if (!rp_append_host_action_line("rp_runner", "host_action_protocol_title=", value)) return 1;
		}
	}

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
		char *core = workbench_core;
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

		char *ops = workbench_ops;
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
			char *row = workbench_row;
			rp_copy_text(row, sizeof(row), "host_action_workbench_row=");
			append_workbench_summary_value(row, sizeof(row), "kind=workbench_task_board_row", "row_id=", "host_action_workbench_row_id=", "usable-workbench:RUN-900:board:task:human_review");
			if (!rp_append_file("rp_runner", row)) return 1;
		}

		char *docs = workbench_docs;
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
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_file_manifest", "files=", "host_action_workbench_manifest_files=", "9");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_file_manifest", "sha_records=", "host_action_workbench_sha_records=", "9");
		}
		if (rp_host_seed_has("kind=workbench_file_verify")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_file_verify=passed;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_file_verify", "manifest=", "host_action_workbench_manifest=", "delivery-manifest.json");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_file_verify", "verified=", "host_action_workbench_verified_files=", "9");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_file_verify", "missing=", "host_action_workbench_missing_files=", "0");
		}
		if (rp_host_seed_has("kind=workbench_export")) {
			has_docs = 1;
			rp_append_text(docs, sizeof(docs), "host_action_workbench_export=ready;");
			append_workbench_summary_value(docs, sizeof(docs), "kind=workbench_export", "bundle=", "host_action_workbench_bundle=", "workbench-bundle.zip");
		}
		if (has_docs) {
			if (!rp_append_file("rp_runner", docs)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_file_manifest") ||
		    rp_host_seed_has("kind=workbench_file_verify")) {
			char *value = workbench_value;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "files=", value, sizeof(workbench_value)) &&
			    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "files=", value, sizeof(workbench_value))) {
				rp_copy_text(value, sizeof(workbench_value), "9");
			}
			if (!rp_append_host_action_line("rp_runner", "host_action_workbench_manifest_files=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_manifest", "sha_records=", value, sizeof(workbench_value)) &&
			    !rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "sha_records=", value, sizeof(workbench_value))) {
				rp_copy_text(value, sizeof(workbench_value), "9");
			}
			if (!rp_append_host_action_line("rp_runner", "host_action_workbench_sha_records=", value)) return 1;
		}
		if (rp_host_seed_has("kind=workbench_file_verify")) {
			char *value = workbench_value;
			if (!rp_append_file("rp_runner", "host_action_workbench_file_verify=passed")) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "verified=", value, sizeof(workbench_value))) {
				rp_copy_text(value, sizeof(workbench_value), "9");
			}
			if (!rp_append_host_action_line("rp_runner", "host_action_workbench_verified_files=", value)) return 1;
			if (!rp_host_seed_copy_value_for_kind("kind=workbench_file_verify", "missing=", value, sizeof(workbench_value))) {
				rp_copy_text(value, sizeof(workbench_value), "0");
			}
			if (!rp_append_host_action_line("rp_runner", "host_action_workbench_missing_files=", value)) return 1;
		}
	}

	if (!rp_append_file("rp_ack", "ack=workbench;msg=research_board;status=ready")) return 1;
	if (kernel_workbench &&
	    !rp_append_file("rp_tool", "tool=agentos.workbench_file_verify"))
		return 1;
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
	printf("rp_workbench: tasks=9 workspace_files=4 runs=4 exports=7 workbenches=8 deliveries=9 project_ops=17 status=ready\n");
	return 0;
}
